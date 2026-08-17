# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""WP-3.1 — scenario comparator: two-run regression gate + longitudinal trend.

Reuses the proven run-over-run comparator shape (the eval apparatus's
``compare_runs``) against the scenario harness's own results.json, joined on the
stable scenario id. Three signals, all load-bearing:

1. **Grade transition.** A scenario that went PASS -> MIXED/FAIL (or MIXED ->
   FAIL) is a regression; the reverse is an improvement. Same grade rank as the
   reference comparator so the two read one severity order.
2. **Coverage-set erosion.** A ``(capability, scenario-id)`` cell exercised by
   the baseline run and absent now — a scenario removed, or one that quietly
   stopped declaring a capability — is flagged at the SAME severity as a
   pass->fail. A suite that silently covers less while going greener is the exact
   failure a per-scenario grade diff is blind to.
3. **Longitudinal per-axis pass-rate.** Each of the six axes gets a pass-rate
   trend (fabrication-guard rate, tool-grounding/routing rate, ...) so a
   cross-cutting quality can be watched run over run independent of the aggregate
   pass/fail — the way the reference apparatus tracked one quality over time.

Exit status: 0 if the candidate has no regression on grade or coverage; 1
otherwise. Use it as the CI gate between a baseline run and a candidate run.

Usage:
    python3 -m intergen.tests.scenario.comparator OLD NEW
    python3 -m intergen.tests.scenario.comparator OLD NEW --json report.json
where OLD/NEW are each a results.json file or a run directory containing one.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# High-is-better grade severity, matching the reference comparator so a drop is
# new_rank < old_rank across the whole toolchain.
_GRADE_RANK = {"PASS": 2, "MIXED": 1, "FAIL": 0, "ERROR": 0}


def _rank(grade: str) -> int:
    return _GRADE_RANK.get(grade, 0)


def _load_results(path_str: str) -> dict:
    """Load a run's results.json from a file path or a run directory."""
    p = Path(path_str)
    if p.is_dir():
        p = p / "results.json"
    if not p.exists():
        raise FileNotFoundError(f"no results.json at {path_str!r} (looked at {p})")
    return json.loads(p.read_text(encoding="utf-8"))


def _grade_map(results: dict) -> dict[str, str]:
    return {s.get("id", ""): s.get("grade", "FAIL")
            for s in results.get("scenarios", [])}


def _coverage_cells(results: dict) -> set[tuple[str, str]]:
    """The (capability, scenario-id) cells a run exercised — every capability a
    scenario in the run declares. A scenario that ran exercised its declared
    capabilities regardless of pass/fail, so the cell is declaration-based; it
    vanishes only when the scenario is dropped or stops declaring the capability
    (the erosion signal)."""
    cells: set[tuple[str, str]] = set()
    for s in results.get("scenarios", []):
        sid = s.get("id", "")
        for cap in s.get("capabilities", []):
            cells.add((cap, sid))
    return cells


def _axis_trend(old_metrics: dict, new_metrics: dict) -> dict[str, dict]:
    """Per-axis pass-rate delta old -> new. A delta is None when either side has
    no scenarios on the axis (reported, never a silent 0)."""
    trend: dict[str, dict] = {}
    for axis in sorted(set(old_metrics) | set(new_metrics)):
        o = old_metrics.get(axis, {}).get("pass_rate")
        n = new_metrics.get(axis, {}).get("pass_rate")
        delta = None if (o is None or n is None) else round(n - o, 4)
        trend[axis] = {"from": o, "to": n, "delta": delta,
                       "regressed": bool(delta is not None and delta < 0)}
    return trend


def compare(old: dict, new: dict) -> dict:
    """Compute the full between-runs diff. Pure — no I/O, no exit."""
    g_old, g_new = _grade_map(old), _grade_map(new)

    grade_regressions, grade_improvements = [], []
    for sid in sorted(set(g_old) & set(g_new)):
        if _rank(g_new[sid]) < _rank(g_old[sid]):
            grade_regressions.append({"id": sid, "from": g_old[sid], "to": g_new[sid]})
        elif _rank(g_new[sid]) > _rank(g_old[sid]):
            grade_improvements.append({"id": sid, "from": g_old[sid], "to": g_new[sid]})

    dropped = sorted(set(g_old) - set(g_new))
    added = sorted(set(g_new) - set(g_old))

    cov_old, cov_new = _coverage_cells(old), _coverage_cells(new)
    vanished = sorted(cov_old - cov_new)
    gained = sorted(cov_new - cov_old)
    new_ids = set(g_new)
    vanished_capability_only = [
        {"capability": cap, "id": sid} for cap, sid in vanished if sid in new_ids]
    vanished_from_removed = [
        {"capability": cap, "id": sid} for cap, sid in vanished if sid not in new_ids]

    axis_trend = _axis_trend(old.get("axis_metrics", {}), new.get("axis_metrics", {}))

    # Regression = any grade drop, any coverage cell lost, or any scenario
    # removed. A per-axis rate slipping is surfaced but is NOT on its own a hard
    # regression (it is a rollup of the grade transitions already counted).
    regression = bool(grade_regressions or vanished or dropped)

    return {
        "regression": regression,
        "grade_regressions": grade_regressions,
        "grade_improvements": grade_improvements,
        "dropped_scenarios": dropped,
        "added_scenarios": added,
        "vanished_cells": [{"capability": c, "id": i} for c, i in vanished],
        "vanished_capability_only": vanished_capability_only,
        "vanished_from_removed_scenarios": vanished_from_removed,
        "gained_cells": [{"capability": c, "id": i} for c, i in gained],
        "axis_trend": axis_trend,
        "counts": {
            "old_scenarios": len(g_old), "new_scenarios": len(g_new),
            "old_cells": len(cov_old), "new_cells": len(cov_new),
        },
    }


def format_report(diff: dict, old_id: str, new_id: str) -> str:
    lines = ["Scenario run comparison", "=" * 60,
             f"  baseline:  {old_id}", f"  candidate: {new_id}"]
    c = diff["counts"]
    lines.append(f"  scenarios: {c['old_scenarios']} -> {c['new_scenarios']}    "
                 f"coverage cells: {c['old_cells']} -> {c['new_cells']}")
    lines.append("")
    lines.append(f"VERDICT: {'REGRESSION' if diff['regression'] else 'CLEAN'}")
    lines.append("")

    def _section(title, items, render):
        if items:
            lines.append(f"{title} ({len(items)}):")
            lines.extend(f"  {render(it)}" for it in items)
            lines.append("")

    _section("GRADE REGRESSIONS (hard)", diff["grade_regressions"],
             lambda r: f"{r['id']}: {r['from']} -> {r['to']}")
    _section("DROPPED SCENARIOS (coverage loss)", diff["dropped_scenarios"],
             lambda i: i)
    _section("VANISHED CELLS — a capability an existing scenario stopped "
             "declaring (coverage erosion)", diff["vanished_capability_only"],
             lambda v: f"{v['capability']}  ({v['id']})")
    _section("GRADE IMPROVEMENTS", diff["grade_improvements"],
             lambda r: f"{r['id']}: {r['from']} -> {r['to']}")
    _section("ADDED SCENARIOS", diff["added_scenarios"], lambda i: i)

    lines.append("Per-axis pass-rate trend:")
    for axis, t in diff["axis_trend"].items():
        fr = "n/a" if t["from"] is None else f"{t['from'] * 100:.0f}%"
        to = "n/a" if t["to"] is None else f"{t['to'] * 100:.0f}%"
        flag = "  <- REGRESSED" if t["regressed"] else ""
        lines.append(f"  {axis:<22} {fr:>5} -> {to:>5}{flag}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Compare two scenario runs; exit 1 on regression (CI gate).")
    ap.add_argument("old", help="baseline results.json or run directory")
    ap.add_argument("new", help="candidate results.json or run directory")
    ap.add_argument("--json", dest="json_out", default=None,
                    help="also write the diff as JSON to this path")
    args = ap.parse_args(argv)

    old, new = _load_results(args.old), _load_results(args.new)
    diff = compare(old, new)
    print(format_report(diff, old.get("run_id", args.old), new.get("run_id", args.new)))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(diff, indent=2), encoding="utf-8")
    return 1 if diff["regression"] else 0


if __name__ == "__main__":
    sys.exit(main())
