[![tests](https://img.shields.io/github/actions/workflow/status/F0Rextasy/cigate/test.yml?branch=master&label=tests&style=flat-square&color=3fb950)](https://github.com/F0Rextasy/cigate/actions/workflows/test.yml)
[![python](https://img.shields.io/badge/python-3.8%2B-3776AB?logo=python&logoColor=white&style=flat-square)](https://www.python.org/)
[![PyYAML](https://img.shields.io/badge/dependency-PyYAML-3fb950?style=flat-square)](https://pyyaml.org/)
[![verdicts](https://img.shields.io/badge/verdicts-deterministic-3fb950?style=flat-square)](#what-it-will-never-do)
[![skills](https://skills.sh/b/F0Rextasy/cigate?style=flat-square)](https://skills.sh/F0Rextasy/cigate)
[![license](https://img.shields.io/badge/license-MIT-3fb950?style=flat-square)](LICENSE)

# cigate

**Your Actions minutes are burning and nothing tells you.** Every push
runs the full matrix because nobody wrote `paths:`, two copy-pasted jobs
do identical work, superseded runs queue with no `concurrency` group,
and last month's failed runs quietly ate the quota. `cigate` reads
`.github/workflows/*.yml` as data, puts a number on the waste, and fails
the build when it crosses your budget.

![cigate terminal demo](assets/demo.svg)

## The problem is real

- The #1 complaint about AI-era development is the bill: usage shocks
  from agents that fire CI constantly make wasted minutes a *daily*
  loss, not a monthly footnote.
- The niche is pre-wave, not owned: `skill-ci-churn` (3 stars),
  `MinuteShield` (1 star) - nobody has a deterministic, offline,
  exit-code workflow-waste gate.
- Every rule here is byte-level static analysis + one arithmetic pass
  over a payload you already can capture with `gh api`.

## Quickstart

```bash
# install the skill into any agent (Claude Code, Codex, Cursor, OpenCode, ...):
npx skills add F0Rextasy/cigate

# or run it directly:
git clone https://github.com/F0Rextasy/cigate

# static pass over this repo, warnings block:
python cigate/scripts/cigate . --strict

# full pass: static + failed-run minute budget from a local capture:
gh api repos/OWNER/REPO/actions/runs --paginate > runs.json
python cigate/scripts/cigate . --runs-json runs.json --max-waste-minutes 60
```

Exit `1` = failure rule or budget exceeded (or any warning with
`--strict`), `0` = lean, `2` = usage error. Python3.8+ and
`pip install pyyaml`; **no network calls, ever** - history mode reads a
file.

## Usage patterns

| Situation | Command |
| --- |---|
| gate every PR that touches workflows | `cigate . --strict` |
| monthly quota review | `gh api repos/O/R/actions/runs --paginate > runs.json && cigate . --runs-json runs.json` |
| hard budget: never burn >60 wasted min/month | add `--max-waste-minutes 60` (fails CI) |
| wider window | `--runs-json runs.json --since-days 90` |
| one rule waived by policy (e.g. org pins tags) | `--allow unpinned-action=org policy` |
| machine-readable report | `--format json` -> `counts`, `waste`, `findings` |

## Rules

Findings name the workflow file (relative to `.github/workflows/`,
forward slashes on every OS):

| Rule | Severity | Fires when |
| --- | --- | --- |
| `unpinned-action` | warn | `uses:` target is not a full 40-char commit SHA (once per ref; local `./` and `docker://` skipped) |
| `missing-paths-filter` | warn | `push`/`pull_request` lacks `paths`/`paths-ignore` **and** the workflow is costly (>=2 jobs or a matrix) - docs-only commits should not run it |
| `no-concurrency` | warn | `pull_request` trigger without a top-level `concurrency` group - rapid pushes queue full runs back to back |
| `duplicate-job` | fail | two jobs with byte-identical bodies under different names - every run pays twice |
| `waste-budget` | fail | failed/cancelled/timed_out minutes in the window exceed `--max-waste-minutes` |
| `malformed-workflow` | warn | YAML cigate could not parse - reported, never silently skipped |

## How the pieces fit

```mermaid
flowchart LR
  W[".github/workflows/*.yml"] --> Y[PyYAML safe_load<br/>on-key quirk handled]
  Y --> S[4 static rules]
  R["gh api runs payload<br/>(local file)"] --> H[minutes arithmetic<br/>failure/cancelled window]
  H --> B{> budget?}
  S --> V{severity}
  B -->|yes| F["FAIL waste-budget"]
  V -->|fail| E["exit 1"]
  V -->|warn + --strict| E
  B -->|no| OK[report suffix]
  V -->|clean| Z["exit 0"]
```

## Evidence

Real output over the committed red example (also the demo above):

```console
$ python scripts/cigate examples/mini-repo --runs-json examples/runs.json --max-waste-minutes 5 --no-color
ci.yml  FAIL  duplicate-job        jobs 'build' and 'test' are identical -- every run pays twice
ci.yml  WARN  missing-paths-filter on: push, pull_request without paths/paths-ignore -- every push runs 2 job(s)
ci.yml  WARN  no-concurrency       pull_request without a concurrency group -- rapid pushes queue full runs back to back
ci.yml  WARN  unpinned-action      uses actions/checkout@v4 -- pin the 40-char commit SHA
-       FAIL  waste-budget         20 min in 1 failed/cancelled run(s) over 30d exceeds budget 5 min

cigate: 2 failure(s), 3 warning(s) across 1 workflow(s), 2 action pin(s) checked; history: 1 failed/cancelled run(s) = 20 min wasted (budget 5 min)
cigate: fix the workflow -- or exempt a rule with:  --allow RULE=<reason>
[exit 1]
```

Machine-readable verdict:

```json
{"ok": false, "counts": {"fail": 2, "warn": 3, "exempt": 0,
                          "workflows": 1, "uses": 2},
 "waste": {"failed_runs": 1, "minutes": 20, "window_days": 30, "budget": 5}}
```

Contract tests drive the real CLI over temp repos - YAML quirks, budget
math, exemption accounting included:

```console
$ python -m unittest discover -s tests -v
...
Ran 9 tests in 1.279s

OK
```

## Exemptions

`--allow RULE=reason` (repeatable) drops that **whole rule**, demands a
non-empty reason, and is echoed as `N exempt by --allow` in the summary
and `counts.exempt` in JSON. Unknown rules are usage errors.

## CI wiring

```yaml
- name: install parser
  run: pip install pyyaml
- name: workflows must be lean
  run: python scripts/cigate . --strict --no-color
- name: the red example must be caught
  run: |
    python scripts/cigate examples/mini-repo --runs-json examples/runs.json --max-waste-minutes 5 || test $? -eq 1
```

This repository's own [test workflow](.github/workflows/test.yml) runs
the middle step against **itself** with `--strict`: an unpinned action or
a missing concurrency group fails the build that would ship it.

## What it will never do

- **Call the network.** History mode parses a file you captured; same
  payload, same verdict, offline.
- **Estimate money it cannot defend.** The unit is *wasted minutes* -
  conversion to currency is yours, with your plan's rates.
- **Drop a rule quietly.** Exemptions are reasoned, counted, and visible
  in every output format.

## One path, many gates - the family

| Repo | What its verdict means |
| --- | ---|
| [dsh-gate](https://github.com/F0Rextasy/dsh-gate) | the shell session actually ran - real commands, real files, real log |
| [sessionaudit](https://github.com/F0Rextasy/sessionaudit) | the session behaved - scope, secrets, destructive acts, self-contradicted claims |
| [cigate](https://github.com/F0Rextasy/cigate) | the workflows burn each minute once - pins, path filters, dedup, budget |
| [ci-triage](https://github.com/F0Rextasy/ci-triage) | one log, one verdict: regression / flaky / infra / pass |
| [docproof](https://github.com/F0Rextasy/docproof) | every README doc snippet is runnable, parsed, and verified in CI |
| [preflight](https://github.com/F0Rextasy/preflight) | the config is safe to ship - semantics, not syntax |
| [prove-it](https://github.com/F0Rextasy/prove-it) | every claim in this README is backed by real, captured output |
| [shipcheck](https://github.com/F0Rextasy/shipcheck) | the artifacts in `dist/` match `src/` - nothing stale ships |
| [testgate](https://github.com/F0Rextasy/testgate) | the tests that ran are the tests that exist - gaps, dupes, skips |
| [bandaid](https://github.com/F0Rextasy/bandaid) | the diff doesn't hide a silent failure - swallowed errors, dead guards |
| [wincompat](https://github.com/F0Rextasy/wincompat) | every path in the tree survives a Windows checkout |

MIT licensed. New waste patterns welcome - attach the workflow snippet
and the minutes it burned.
