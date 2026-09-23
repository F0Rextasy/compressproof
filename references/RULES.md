# Transform catalogue

Input: UTF-8 text files. Non-UTF-8 input -> usage error (exit 2);
there is nothing lossy to prove about bytes that are not text.

Bundles: deterministic JSON (`sort_keys`, compact separators, one
trailing newline), so the same input bytes always produce the same
bundle bytes. Dedupe keys are 16-hex SHA-256 prefixes, extended by 4
hex chars on a prefix collision -- still deterministic, still exact.

## Modes

| Mode | Transform id | Body | Restore |
| --- | --- | --- | --- |
| `dedupe` | `dedupe-v1:line-sha256-index` | first occurrence stored inline as `{"t":"def","h":key}`, repeats as `{"t":"ref","h":key}` | look every body key up in `index` |
| `strip` | `strip-v1:trail+blank` | trailing-whitespace-free lines; blank runs collapsed to one | re-attach each recorded `trail` and replay `skip_trails` |
| `extract` | `extract-v1:line-filter+sidecar` (`:substring` or `:regex`) | kept lines + their original line numbers in `kept` | interleave `body` and `sidecar.dropped` by line number |

`--mode extract` needs a non-empty `--query` and `--sidecar FILE`;
`--query` is a substring unless `--regex` is passed. `--sidecar` /
`--query` with any other mode is a usage error.

## Verify (the guarantee)

`verify --orig O --bundle C [--sidecar S]` restores the bundle and
compares bytes to the original:

- restored bytes != original bytes -> `silent-loss` (fail, exit 1),
  with restored-vs-original SHA-256 prefixes printed as
  `measured` vs `threshold`.
- extract bundle whose sidecar bytes do not hash to the recorded
  `sidecar_sha256` -> `sidecar-mismatch` (fail, exit 1) before any
  restore is attempted.
- extract bundle without `--sidecar`, unknown mode, or unreadable
  files -> usage error (exit 2).

`compress` self-verifies the same way before it prints: a non-roundtrip
compress exits 1 with a `silent-loss` finding.

## Proof harness (offline by design)

`proof --corpus DIR --needles FILE [--results FILE]`:

1. **Round-trip table**: every corpus file x every mode -> restored
   SHA-256 must equal the original. Any mismatch fails the run with
   `roundtrip-failure`.
2. **Answer-equivalence oracle**: each needles entry lists exact
   substring questions (plus `expect: false` absence checks, which the
   harness validates against the corpus first so stale fixtures fail
   loudly). Naive `in` scans over the full original are compared with
   the same scan over the compressed representation (bundle bytes plus
   sidecar bytes for extract). Any divergence fails with
   `answer-divergence`.
3. **Baselines on the same bytes**: head-truncation at the extract
   byte budget (position-blind, loses answers) and `gzip -6` with a
   raw-substring scan of the compressed bytes (binary, must fully
   decompress to query). Our claim is NOT "smaller than gzip" -- it is
   "reversible + query-answerable + N/N answers kept".

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | round-trip clean / oracle 100% |
| 1 | `silent-loss`, `sidecar-mismatch`, `roundtrip-failure`, or `answer-divergence` |
| 2 | usage: bad mode, extract without `--sidecar`/`--query`, missing or unparseable files, stale needles spec |

## Invariants

- Round-trip is a pure function of the input bytes; the proof result
  is a pure function of (corpus bytes, needles spec).
- Row order is deterministic: corpus files alphabetical, modes in
  `dedupe|strip|extract` order.
- No network calls, ever. Python 3.8+ stdlib only.
