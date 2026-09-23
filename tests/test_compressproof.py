"""Contract tests for compressproof. Run: python -m unittest discover -s tests -v

The real CLI is driven over temp files: every mode must restore
byte-exact, verify must catch tampering, and the committed proof
harness (examples/corpus + examples/needles.json) must pass with a
100% answer-equivalence oracle.
"""

import gzip
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import warnings

warnings.simplefilter("ignore", ResourceWarning)  # temp-file handles die

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "compressproof")
CORPUS = os.path.join(ROOT, "examples", "corpus")
NEEDLES = os.path.join(ROOT, "examples", "needles.json")
RESULTS = os.path.join(ROOT, "examples", "results", "proof.json")

DUP = ("alpha beta gamma\n" * 3 + "delta\n" + "alpha beta gamma\n" * 2)
PAD = "one   \n\t\ntwo\t\n\n\nthree\n"
ONE_LINE = "single line, no trailing newline"


def run_cli(*args):
    proc = subprocess.run([sys.executable, SCRIPT, *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=ROOT)
    return proc.returncode, proc.stdout, proc.stderr


def read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(obj, handle)


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def tmp_write(tmp, name, data):
    path = os.path.join(tmp, name)
    with open(path, "wb") as handle:
        handle.write(data if isinstance(data, bytes)
                     else data.encode("utf-8"))
    return path

def compress_verify(tmp, text, mode, query=None):
    src = tmp_write(tmp, "orig.txt", text)
    bundle = os.path.join(tmp, "c.json")
    sidecar = os.path.join(tmp, "c.sidecar") if mode == "extract" else None
    args = ["compress", "--mode", mode, "--in", src, "--out", bundle]
    if mode == "extract":
        args += ["--sidecar", sidecar, "--query", query or "x"]
    code, out, _ = run_cli("--format", "json", *args)
    assert code == 0, out
    data = json.loads(out)
    vargs = ["verify", "--orig", src, "--bundle", bundle]
    if sidecar:
        vargs += ["--sidecar", sidecar]
    code, vout, _ = run_cli("--format", "json", *vargs)
    assert code == 0, vout
    return data, json.loads(vout)


class CompressproofContract(unittest.TestCase):
    def test_every_mode_roundtrips_byte_exact(self):
        fixtures = [DUP, PAD, ONE_LINE, "", "trailing\n",
                    "line\r\ncrlf mix  \n\nend\n", "emoji \U0001f600 ok\n"]
        for mode in ("dedupe", "strip"):
            for text in fixtures:
                with tempfile.TemporaryDirectory() as tmp:
                    _, vout = compress_verify(tmp, text, mode)
                    self.assertTrue(vout["roundtrip"])
                    self.assertEqual(vout["sha256_in"], vout["sha256_out"])
        with tempfile.TemporaryDirectory() as tmp:
            _, vout = compress_verify(tmp, DUP + "needle-ZZ here\n",
                                      "extract", query="needle-ZZ")
            self.assertTrue(vout["roundtrip"])

    def test_verify_catches_silent_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = tmp_write(tmp, "orig.txt", DUP)
            bundle = os.path.join(tmp, "c.json")
            code, _, _ = run_cli("compress", "--mode", "dedupe",
                                 "--in", src, "--out", bundle)
            self.assertEqual(code, 0)
            payload = read_json(bundle)
            payload["body"][0], payload["body"][3] = (
                {"t": "ref", "h": payload["body"][3]["h"]},
                {"t": "ref", "h": payload["body"][0]["h"]})
            write_json(bundle, payload)
            code, out, _ = run_cli("--format", "json", "verify",
                                   "--orig", src, "--bundle", bundle)
            self.assertEqual(code, 1, out)
            data = json.loads(out)
            self.assertFalse(data["roundtrip"])
            self.assertEqual(data["findings"][0]["rule"], "silent-loss")

    def test_verify_catches_sidecar_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = tmp_write(tmp, "orig.txt", "keep A\nkeep B needle\n")
            bundle = os.path.join(tmp, "c.json")
            sidecar = os.path.join(tmp, "c.sidecar")
            run_cli("compress", "--mode", "extract", "--in", src,
                    "--out", bundle, "--sidecar", sidecar,
                    "--query", "needle")
            with open(sidecar, "ab") as handle:
                handle.write(b" ")
            code, out, _ = run_cli("--format", "json", "verify",
                                   "--orig", src, "--bundle", bundle,
                                   "--sidecar", sidecar)
            self.assertEqual(code, 1, out)
            self.assertEqual(json.loads(out)["findings"][0]["rule"],
                             "sidecar-mismatch")

    def test_dedupe_report_numbers_are_honest(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, vout = compress_verify(tmp, DUP, "dedupe")
            self.assertEqual(data["bytes_in"], len(DUP.encode("utf-8")))
            self.assertEqual(data["bytes_out"],
                             os.path.getsize(os.path.join(tmp, "c.json")))
            self.assertEqual(data["sha256_in"],
                             hashlib.sha256(
                                 DUP.encode("utf-8")).hexdigest())
            self.assertAlmostEqual(data["ratio"],
                                   data["bytes_out"] / data["bytes_in"])
            self.assertEqual(vout["ratio"], data["ratio"])

    def test_extract_sidecar_holds_dropped_bytes(self):
        text = "keep needle one\n" * 4 + "drop me\n" * 6
        with tempfile.TemporaryDirectory() as tmp:
            src = tmp_write(tmp, "orig.txt", text)
            bundle = os.path.join(tmp, "c.json")
            sidecar = os.path.join(tmp, "c.sidecar")
            code, out, _ = run_cli(
                "--format", "json", "compress", "--mode", "extract",
                "--in", src, "--out", bundle, "--sidecar", sidecar,
                "--query", "needle")
            self.assertEqual(code, 0, out)
            side = read_json(sidecar)
            self.assertEqual(len(side["dropped"]), 6)
            kept = read_json(bundle)["body"]
            self.assertTrue(all("needle" in line for line in kept))

    def test_usage_errors_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = tmp_write(tmp, "orig.txt", "x\n")
            code, _, err = run_cli("compress", "--mode", "nope",
                                   "--in", src, "--out", "o.json")
            self.assertEqual(code, 2)
            self.assertIn("--mode", err)
            code, _, err = run_cli("compress", "--mode", "extract",
                                   "--in", src, "--out", "o.json")
            self.assertEqual(code, 2)
            self.assertIn("--sidecar", err)
            code, _, err = run_cli("verify", "--orig", src,
                                   "--bundle", os.path.join(tmp, "nope.json"))
            self.assertEqual(code, 2)

    def test_proof_harness_passes_with_full_oracle(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = os.path.join(tmp, "proof.json")
            code, out, _ = run_cli("--no-color", "proof", "--corpus", CORPUS,
                                   "--needles", NEEDLES, "--results", results)
            self.assertEqual(code, 0, out)
            data = read_json(results)
            self.assertTrue(data["ok"])
            oracle = data["oracle"]
            self.assertEqual(oracle["matched"], oracle["total"])
            self.assertGreater(oracle["total"], 0)
            # every corpus file x every mode restored byte-exact
            for name, entry in data["files"].items():
                for mode, stats in entry["modes"].items():
                    self.assertTrue(stats["roundtrip"],
                                    "%s/%s" % (name, mode))
                    self.assertEqual(stats["sha256_in"], stats["sha256_out"])
            # committed results file matches the harness output
            committed = read_json(RESULTS)
            self.assertEqual(committed["oracle"], oracle)
            for name, entry in committed["files"].items():
                for mode, stats in entry["modes"].items():
                    self.assertEqual(
                        stats["sha256_in"],
                        data["files"][name]["modes"][mode]["sha256_in"])

    def test_baseline_table_is_honest(self):
        data = read_json(RESULTS)
        base = data["oracle"]
        # head-truncation at equal byte budget must lose answers (it is
        # position-blind); our claim is retention, not beating gzip size.
        self.assertLess(base["trunc_kept"], base["total"])
        # gzip bytes contain compressed data, not queryable text: a raw
        # substring scan must not retain every answer.
        self.assertLess(base["gzip_direct_kept"], base["total"])
        for name, entry in data["files"].items():
            raw = read_bytes(os.path.join(CORPUS, name))
            self.assertEqual(entry["bytes_in"], len(raw))
            self.assertEqual(entry["baselines"]["gzip_bytes"],
                             len(gzip.compress(raw, compresslevel=6)))

    def test_deterministic_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = tmp_write(tmp, "orig.txt", DUP)
            first = second = None
            for i in ("a.json", "b.json"):
                out = os.path.join(tmp, i)
                code, _, _ = run_cli("compress", "--mode", "dedupe",
                                     "--in", src, "--out", out)
                self.assertEqual(code, 0)
                content = read_bytes(out)
                first = content if first is None else first
                second = content
            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
