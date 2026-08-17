# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Consolidate many discovery-run findings ledgers into ONE decision-grade ledger.

M8-6 leg C / M8-7 leg 2. Reads each run's `findings-ledger.jsonl` (mining it via
mine_findings.mine() first if absent), tags every finding with its run-id and its
corpus half (`sf-` = surface-flex / code-grounded; `dd-` = demand-distribution),
dedups ACROSS runs by (class, id, turn_index) so a finding surfaced in five runs
is ONE consolidated row carrying the set of runs it appeared in, and emits a
severity-ordered ledger + a class x severity summary + a human markdown table.

DISCOVERY, not pass/fail: these are candidate findings the operator uses to cut
the routing tweak waves (the lane split — RUNTIME defects are ledger rows here,
NOT fixed in this task). Every consolidated class names representative turn ids +
evidence + which corpus half surfaced it, so the ledger is decision-grade.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from intergen.tests.demand_corpus.mine_findings import SEVERITY, mine, _load_jsonl


def _half(finding_id: str) -> str:
    """Corpus half from the entry id prefix. Robust to BOTH id schemes: the
    post-reconciliation kebab ids (`sf-…` / `dd-…`) AND the earlier surface-flex
    smoke ids (`sf_compound_pure-…`). Split on the first `-` or `_`, map the
    alpha prefix: sf = surface-flex (code-grounded), dd = demand-distribution."""
    prefix = re.split(r"[-_]", finding_id, maxsplit=1)[0].lower()
    if prefix == "sf":
        return "surface"
    if prefix == "dd":
        return "demand"
    return "other"


def _ensure_ledger(run_dir: Path) -> list[dict]:
    """Return a run's findings, mining it first if the ledger is absent."""
    ledger = run_dir / "findings-ledger.jsonl"
    if not ledger.exists() and (run_dir / "results.jsonl").exists():
        mine(run_dir)
    return _load_jsonl(ledger)


def consolidate(run_dirs: list[Path]) -> dict[str, Any]:
    # dedup key -> merged finding (a finding seen in N runs is ONE row)
    merged: dict[tuple, dict] = {}
    runs_meta: list[dict] = []
    for rd in run_dirs:
        findings = _ensure_ledger(rd)
        results = _load_jsonl(rd / "results.jsonl")
        runs_meta.append({
            "run_id": rd.name,
            "results_entries": len(results),
            "observed_turns": sum(len(e.get("observed", [])) for e in results),
            "findings": len(findings),
        })
        for f in findings:
            key = (f["class"], f["id"], f.get("turn_index", 0))
            if key not in merged:
                m = dict(f)
                m["runs"] = [rd.name]
                m["occurrences"] = 1
                m["half"] = _half(f["id"])
                merged[key] = m
            else:
                m = merged[key]
                if rd.name not in m["runs"]:
                    m["runs"].append(rd.name)
                m["occurrences"] += 1
                # keep the strongest severity if runs ever disagree
                if SEVERITY.index(f["severity"]) < SEVERITY.index(m["severity"]):
                    m["severity"] = f["severity"]

    consolidated = sorted(
        merged.values(),
        key=lambda f: (SEVERITY.index(f["severity"]), f["class"], f["id"]))

    # class x severity aggregation, with which-half split + representative ids
    by_class: dict[str, dict] = defaultdict(lambda: {
        "severity": "low", "count": 0, "halves": defaultdict(int),
        "categories": defaultdict(int), "runs": set(), "reps": []})
    for f in consolidated:
        c = by_class[f["class"]]
        if SEVERITY.index(f["severity"]) < SEVERITY.index(c["severity"]) or c["count"] == 0:
            c["severity"] = f["severity"]
        c["count"] += 1
        c["halves"][f["half"]] += 1
        c["categories"][f.get("category", "")] += 1
        c["runs"].update(f["runs"])
        if len(c["reps"]) < 5:
            c["reps"].append({
                "id": f["id"], "half": f["half"],
                "category": f.get("category", ""),
                "user": f.get("user", ""),
                "evidence": (f.get("evidence", "") or "")[:200],
                "tool_calls": f.get("tool_calls", []),
                "runs": f["runs"],
            })

    class_rows = []
    for cls, c in by_class.items():
        class_rows.append({
            "class": cls, "severity": c["severity"], "count": c["count"],
            "by_half": dict(c["halves"]), "by_category": dict(
                sorted(c["categories"].items(), key=lambda kv: -kv[1])),
            "runs": sorted(c["runs"]), "representatives": c["reps"],
        })
    class_rows.sort(key=lambda r: (SEVERITY.index(r["severity"]), -r["count"], r["class"]))

    by_sev: dict[str, int] = defaultdict(int)
    half_split: dict[str, int] = defaultdict(int)
    for f in consolidated:
        by_sev[f["severity"]] += 1
        half_split[f["half"]] += 1

    return {
        "runs": runs_meta,
        "unique_findings": len(consolidated),
        "raw_findings": sum(r["findings"] for r in runs_meta),
        "by_severity": dict(sorted(by_sev.items(), key=lambda kv: SEVERITY.index(kv[0]))),
        "by_half": dict(half_split),
        "classes": class_rows,
        "_consolidated": consolidated,
    }


def _write_markdown(summary: dict, out: Path) -> None:
    lines = ["# Consolidated discovery findings ledger", ""]
    lines.append(f"Runs consolidated: {len(summary['runs'])} "
                 f"({', '.join(r['run_id'] for r in summary['runs'])})")
    lines.append(f"Unique findings: **{summary['unique_findings']}** "
                 f"(from {summary['raw_findings']} raw, deduped across runs)")
    sev = summary["by_severity"]
    lines.append("By severity: " + ", ".join(f"{k} {v}" for k, v in sev.items()))
    half = summary["by_half"]
    lines.append("By corpus half: " + ", ".join(f"{k} {v}" for k, v in half.items()))
    lines.append("")
    lines.append("| Severity | Class | Count | surface / demand | top categories | runs |")
    lines.append("|---|---|---:|---|---|---|")
    for r in summary["classes"]:
        halfstr = f"{r['by_half'].get('surface', 0)} / {r['by_half'].get('demand', 0)}"
        cats = ", ".join(f"{k}:{v}" for k, v in list(r["by_category"].items())[:3])
        lines.append(f"| {r['severity']} | {r['class']} | {r['count']} | "
                     f"{halfstr} | {cats} | {len(r['runs'])} |")
    lines.append("")
    lines.append("## Representative turns per class (most severe first)")
    for r in summary["classes"]:
        lines.append("")
        lines.append(f"### [{r['severity']}] {r['class']} — {r['count']} findings")
        for rep in r["representatives"]:
            tc = f" tools={rep['tool_calls']}" if rep["tool_calls"] else ""
            lines.append(f"- `{rep['id']}` ({rep['half']}/{rep['category']}){tc}: "
                         f"\"{rep['user'][:100]}\"")
            if rep["evidence"]:
                lines.append(f"    - got: \"{rep['evidence'][:160]}\"")
    out.write_text("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Consolidate discovery findings ledgers")
    ap.add_argument("run_dirs", nargs="+", help="discovery run directories")
    ap.add_argument("--out-dir", default=None,
                    help="where to write the consolidated ledger (default: cwd)")
    args = ap.parse_args()
    run_dirs = [Path(d) for d in args.run_dirs]
    summary = consolidate(run_dirs)
    out_dir = Path(args.out_dir) if args.out_dir else Path.cwd()
    out_dir.mkdir(parents=True, exist_ok=True)

    consolidated = summary.pop("_consolidated")
    with (out_dir / "consolidated-ledger.jsonl").open("w") as fh:
        for f in consolidated:
            f = dict(f)
            f.pop("runs_set", None)
            fh.write(json.dumps(f, ensure_ascii=False) + "\n")
    (out_dir / "consolidated-summary.json").write_text(json.dumps(summary, indent=1))
    _write_markdown(summary, out_dir / "consolidated-summary.md")
    print(json.dumps({k: summary[k] for k in
                      ("unique_findings", "raw_findings", "by_severity", "by_half")},
                     indent=1))
    print(f"wrote consolidated-ledger.jsonl + summary.json + summary.md to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
