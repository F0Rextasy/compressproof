#!/usr/bin/env python3
"""cigate -- gate GitHub Actions workflows for waste and policy drift.

Static, deterministic rules over .github/workflows/*.yml: unpinned action
pins, triggers without path filters, duplicate jobs, pull_request without
a concurrency group. Optional history mode reads a captured
`gh api repos/OWNER/REPO/actions/runs` payload (--runs-json) and fails
when wasted minutes on failed/cancelled runs exceed --max-waste-minutes.
Exit 1 on failure-class findings; warnings block only with --strict.
"""

import argparse
import datetime
import glob
import json
import os
import re
import sys

try:
    import yaml
except ImportError:  # pragma: no cover - dependency guard
    print("cigate: PyYAML is required (pip install pyyaml)", file=sys.stderr)
    sys.exit(2)

SHA = re.compile(r"^[0-9a-f]{40}$")
WASTE_CONCLUSIONS = {"failure", "cancelled", "timed_out"}

RULES = {
    "unpinned-action": (
        "warn",
        "pin `uses:` to the full 40-char commit SHA, tag in a comment"),
    "missing-paths-filter": (
        "warn",
        "add paths/paths-ignore so docs-only changes do not run the "
        "matrix"),
    "no-concurrency": (
        "warn",
        "add a top-level concurrency group to cancel superseded runs"),
    "duplicate-job": (
        "fail",
        "two jobs run identical steps -- merge them or vary the work"),
    "waste-budget": (
        "fail",
        "failed/cancelled runs over budget -- fix the cause or raise "
        "--max-waste-minutes deliberately"),
    "malformed-workflow": (
        "warn", "unparseable workflow YAML -- cigate could not audit it"),
}

BOLD = "\033[1m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
RESET = "\033[0m"


def paint(text, *codes, color=True):
    if not color:
        return text
    return "".join(codes) + text + RESET


def find_workflows(root):
    if not os.path.isdir(root):
        print("cigate: not a directory: %s" % root, file=sys.stderr)
        sys.exit(2)
    directory = os.path.join(root, ".github", "workflows")
    files = sorted(set(glob.glob(os.path.join(directory, "*.yml")))
                   | set(glob.glob(os.path.join(directory, "*.yaml"))))
    if not files:
        print("cigate: no workflow files under %s" % directory,
              file=sys.stderr)
        sys.exit(2)
    return files


def triggers_of(data):
    raw = data.get("on", data.get(True))  # PyYAML reads `on` as True
    if isinstance(raw, dict):
        return raw, list(raw)
    if isinstance(raw, list):
        return {}, [str(t) for t in raw]
    if isinstance(raw, str):
        return {}, [raw]
    return {}, []


def uses_refs(data):
    """All `uses:` strings in a workflow document (steps + jobs.container)."""
    refs = []
    jobs = data.get("jobs") or {}
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        container = job.get("container")
        if isinstance(container, dict) and container.get("image"):
            refs.append("docker://" + str(container["image"]))
        for step in job.get("steps") or []:
            if isinstance(step, dict) and step.get("uses"):
                refs.append(str(step["uses"]))
    return refs


def audit_workflow(path, rel, rows):
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        if not isinstance(data, dict):
            raise ValueError("workflow is not a mapping")
    except (yaml.YAMLError, ValueError, OSError) as exc:
        rows.append({"file": rel, "line": 0, "rule": "malformed-workflow",
                     "severity": "warn",
                     "message": str(exc).splitlines()[0][:90],
                     "suggestion": RULES["malformed-workflow"][1]})
        return {"workflows": 1, "uses": 0}

    jobs = data.get("jobs") if isinstance(data.get("jobs"), dict) else {}
    on_map, trigger_list = triggers_of(data)
    stats = {"workflows": 1, "uses": 0}

    def add(rule, message):
        severity, suggestion = RULES[rule]
        rows.append({"file": rel, "line": 0, "rule": rule,
                     "severity": severity, "message": message,
                     "suggestion": suggestion})

    # -- action pins (deduped per workflow)
    seen_unpinned = set()
    for ref in uses_refs(data):
        stats["uses"] += 1
        if "@" not in ref or ref.startswith("./") or ref.startswith("docker://"):
            continue
        target = ref.split("@", 1)[1]
        if not SHA.match(target) and ref not in seen_unpinned:
            seen_unpinned.add(ref)
            add("unpinned-action",
                "uses %s -- pin the 40-char commit SHA" % ref)

    # -- path filters on push / pull_request
    missing = []
    for trigger in ("push", "pull_request"):
        if trigger not in trigger_list:
            continue
        entry = on_map.get(trigger) if isinstance(on_map, dict) else None
        has_paths = isinstance(entry, dict) and (
            "paths" in entry or "paths-ignore" in entry)
        if not has_paths:
            missing.append(trigger)
    costly = len(jobs) >= 2 or any(
        isinstance((job.get("strategy") or {}).get("matrix"), dict)
        for job in jobs.values() if isinstance(job, dict))
    if missing and costly:
        add("missing-paths-filter",
            "on: %s without paths/paths-ignore -- every push runs %d job(s)"
            % (", ".join(missing), len(jobs)))

    # -- concurrency for PR-triggered workflows
    if "pull_request" in trigger_list and "concurrency" not in data:
        add("no-concurrency",
            "pull_request without a concurrency group -- rapid pushes "
            "queue full runs back to back")

    # -- duplicate jobs (identical bodies, different names)
    by_body = {}
    for name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        body = json.dumps(job, sort_keys=True)
        if body in by_body:
            add("duplicate-job",
                "jobs '%s' and '%s' are identical -- every run pays twice"
                % (by_body[body], name))
        else:
            by_body[body] = name
    return stats


def parse_when(text):
    if not text:
        return None
    try:
        return datetime.datetime.fromisoformat(
            str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


def waste_minutes(runs_payload, since_days):
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(days=since_days)
    wasted = 0
    failed = 0
    if not isinstance(runs_payload, list):
        raise ValueError("runs JSON must be an array of run objects")
    for run in runs_payload:
        if not isinstance(run, dict) or run.get("conclusion") not in (
                WASTE_CONCLUSIONS):
            continue
        start = parse_when(run.get("run_started_at") or run.get("created_at"))
        end = parse_when(run.get("updated_at"))
        if start is None or end is None or start < cutoff:
            continue
        seconds = max(0, int((end - start).total_seconds()))
        wasted += int(seconds / 60)
        failed += 1
    return failed, wasted


def render(rows, stats, waste, budget, exempt, strict, color):
    width = min(34, max([len(r["file"]) for r in rows] or [8]))
    for row in sorted(rows,
                      key=lambda r: (r["file"] == "-", r["file"],
                                     r["rule"])):
        severity = row["severity"]
        label = "FAIL" if severity == "fail" else "WARN"
        codes = (RED, BOLD) if severity == "fail" else (YELLOW, BOLD)
        print("%-*s  %s  %-20s %s"
              % (width, row["file"], paint(label, *codes, color=color),
                 row["rule"], row["message"]))
    if rows:
        print()
    fails = sum(1 for r in rows if r["severity"] == "fail")
    warns = sum(1 for r in rows if r["severity"] == "warn")
    tail = "%d workflow(s), %d action pin(s) checked" % (
        stats["workflows"], stats["uses"])
    if waste is not None:
        failed, minutes = waste
        tail += "; history: %d failed/cancelled run(s) = %d min wasted" % (
            failed, minutes)
        if budget is not None:
            tail += " (budget %d min)" % budget
    if exempt:
        tail += ", %d exempt by --allow" % exempt
    if fails:
        print(paint("cigate: %d failure(s), %d warning(s) across %s"
                    % (fails, warns, tail), BOLD, RED, color=color))
        print(paint("cigate: fix the workflow -- or exempt a rule with:  "
                    "--allow RULE=<reason>", BOLD, color=color))
    else:
        print(paint("cigate: ok -- %d failure(s), %d warning(s) across %s"
                    % (fails, warns, tail), BOLD, color=color))
    if strict and warns:
        print(paint("cigate: --strict blocks on %d warning(s)" % warns,
                    BOLD, RED, color=color))
    return fails, warns


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="cigate",
        description="Gate GitHub Actions workflows for waste: unpinned "
                    "actions, missing path filters, duplicate jobs, no "
                    "concurrency group, failed-run minute budget.")
    parser.add_argument("path", nargs="?", default=".",
                        help="repository root containing "
                             ".github/workflows (default .)")
    parser.add_argument("--runs-json", metavar="FILE",
                        help="captured `gh api repos/O/R/actions/runs` "
                             "payload for history mode")
    parser.add_argument("--max-waste-minutes", type=int, metavar="N",
                        help="fail when failed/cancelled minutes in "
                             "--since-days exceed N")
    parser.add_argument("--since-days", type=int, default=30,
                        help="history window in days (default 30)")
    parser.add_argument("--allow", action="append", default=[],
                        metavar="RULE=reason",
                        help="exempt one rule with a reason (repeatable)")
    parser.add_argument("--strict", action="store_true",
                        help="warnings block too")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args(argv)

    allow = set()
    for entry in args.allow:
        if "=" not in entry:
            print("cigate: --allow expects RULE=reason, got: %s" % entry,
                  file=sys.stderr)
            return 2
        rule, reason = entry.split("=", 1)
        if rule not in RULES or not reason.strip():
            print("cigate: unknown rule or empty reason in: %s" % entry,
                  file=sys.stderr)
            return 2
        allow.add(rule)

    files = find_workflows(args.path)
    workflows_dir = os.path.join(args.path, ".github", "workflows")
    rows = []
    stats = {"workflows": 0, "uses": 0}
    for path in files:
        rel = os.path.relpath(path, workflows_dir).replace(os.sep, "/")
        file_stats = audit_workflow(path, rel, rows)
        stats["workflows"] += file_stats["workflows"]
        stats["uses"] += file_stats["uses"]

    waste = None
    if args.runs_json:
        try:
            with open(args.runs_json, encoding="utf-8") as handle:
                payload = json.load(handle)
            waste = waste_minutes(payload, args.since_days)
        except (OSError, ValueError) as exc:
            print("cigate: cannot read runs JSON: %s" % exc, file=sys.stderr)
            return 2
        if args.max_waste_minutes is not None:
            failed, minutes = waste
            if minutes > args.max_waste_minutes:
                rows.append({
                    "file": "-", "line": 0, "rule": "waste-budget",
                    "severity": "fail",
                    "message": "%d min in %d failed/cancelled run(s) over "
                               "%dd exceeds budget %d min"
                               % (minutes, failed, args.since_days,
                                  args.max_waste_minutes),
                    "suggestion": RULES["waste-budget"][1]})
    elif args.max_waste_minutes is not None:
        print("cigate: --max-waste-minutes needs --runs-json",
              file=sys.stderr)
        return 2

    kept = [r for r in rows if r["rule"] not in allow]
    exempt = len(rows) - len(kept)
    rows = kept
    fails = sum(1 for r in rows if r["severity"] == "fail")
    warns = sum(1 for r in rows if r["severity"] == "warn")
    ok = fails == 0 and not (args.strict and warns > 0)

    if args.format == "json":
        print(json.dumps(
            {"ok": ok, "counts": {"fail": fails, "warn": warns,
                                  "exempt": exempt, **stats},
             "waste": ({"failed_runs": waste[0], "minutes": waste[1],
                        "window_days": args.since_days,
                        "budget": args.max_waste_minutes}
                       if waste else None),
             "findings": rows}, indent=2))
    else:
        render(rows, stats, waste, args.max_waste_minutes, exempt,
               args.strict, not args.no_color)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
