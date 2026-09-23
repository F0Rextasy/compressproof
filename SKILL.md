---
name: compressproof
description: "Verifies context compression is reversible and answer-preserving: dedupe repeated blocks by content hash, reversible whitespace-strip transforms with recorded ops, and query-extract with dropped bytes kept in a sidecar so verify restores byte-exact SHA-256. Ships a proof harness over a committed corpus with a round-trip table plus an answer-equivalence oracle. Use when shrinking agent context, logs, or retrieved files. Exit 0 is clean, exit 1 is silent-loss or answer divergence, exit 2 is usage error."
license: MIT
compatibility: "Requires Python 3.8+ stdlib only (no PyYAML, no network, no GPU). Works on Windows and Linux. Deterministic verdicts: same input bytes give the same bundle bytes and exit code."
metadata:
  author: F0Rextasy
  version: "1.0"
---

# compressproof

Context gets truncated and answers silently vanish. `compressproof`
shrinks text three lossless ways -- and **proves** every byte survives:
`verify` restores the original and compares SHA-256, and the `proof`
harness answers the same needle questions on the original and the
compressed form.

## The one rule

You may not ship a smaller context you have not restored:

```bash
python scripts/compressproof compress --mode extract --in big.log --out small.json --sidecar small.sidecar --query ERROR
python scripts/compressproof verify --orig big.log --bundle small.json --sidecar small.sidecar
```

- **exit 0** - round-trip byte-exact, oracle answers match.
- **exit 1** - `silent-loss` (restored bytes differ), `sidecar-mismatch`,
  or an `answer-divergence`.
- **exit 2** - usage: bad `--mode`, `extract` without `--sidecar`/`--query`,
  missing files.

## Protocol

1. **Compress one file**: `--mode dedupe|strip|extract`.
   `extract` needs `--query` (substring, or regex with `--regex`) and
   `--sidecar FILE` for the dropped bytes.
2. **Verify it**: `--orig` + `--bundle` (+ `--sidecar` for extract).
   The restored bytes must SHA-256-match the original.
3. **Prove the corpus**: `proof --corpus examples/corpus
   --needles examples/needles.json --results examples/results/proof.json`
   runs every file x mode plus the needle oracle and the
   truncation/gzip baselines. Fails CI when any answer diverges.
4. **Read findings** as `file  FAIL  rule  message` with measured vs
   recorded hashes printed.

## Modes

| Mode | What it does | When the bytes shrink |
| --- | --- | --- |
| `dedupe` | first occurrence stored once, repeats become short-hash refs (`dedupe-v1:line-sha256-index`) | repeated tool outputs, re-read files, duplicated log blocks |
| `strip` | trailing whitespace removed, blank runs collapsed, every byte recorded (`strip-v1:trail+blank`) | padded logs, noisy captures |
| `extract` | keep only `--query` lines, dropped lines in the sidecar (`extract-v1:line-filter+sidecar`) | needle search over a big log |

```console
$ python scripts/compressproof --no-color verify --orig examples/red/orig.txt --bundle examples/red/clean.json
compressproof: verify examples/red/clean.json: roundtrip=ok bytes_in=91 bytes_out=517 ratio=568.1% sha256_in=372c230d0f1f4d53250acd855b7365c86bd81dfe9a6f67389ec4bc9c194583e3 sha256_out=372c230d0f1f4d53250acd855b7365c86bd81dfe9a6f67389ec4bc9c194583e3
[exit 0]
$ python scripts/compressproof --no-color verify --orig examples/red/orig.txt --bundle examples/red/tampered.json
examples/red/tampered.json  FAIL  silent-loss      restored sha256 86bc18948a5bf81f != original 372c230d0f1f4d53

compressproof: verify examples/red/tampered.json: 1 finding(s) -- the bytes would NOT survive this transform
[exit 1]
```

## Reporting back

1. Counts: `bytes_in` / `bytes_out` / `ratio` / `sha256_in` / `sha256_out`.
2. Proof table: every corpus file x every mode with round-trip status.
3. Oracle: matched/total needle answers, plus truncation and gzip-direct
   retention at the same budget.
4. Command and exit code.
