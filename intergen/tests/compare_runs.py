# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Run-over-run comparator for the eval harness.

Diffs two harness runs (each a results.json, or a run directory containing one)
and reports regressions. Two axes, both load-bearing:

  1. Per-conversation GRADE diff — a conversation that went PASS→MIXED/FAIL (or
     MIXED→FAIL) is a regression; the reverse is an improvement.

  2. Cell-COVERAGE-SET diff — a capability/conversation cell that the earlier run
     covered and the new run does NOT is coverage erosion, flagged at the SAME
     severity as a pass→fail. This is the half a per-conversation grade diff is
     blind to: deleting a test, or a conversation that quietly stops exercising a
     capability, makes the suite greener while covering less. missing-cell=fail
     applied ACROSS runs, not just within one.

It also reports coverage GAPS against the canonical capability inventory (a
gated capability covered by nothing — write_file/run_command were exactly this),
so a gap that exists in both runs stays visible even though it is not a
between-runs regression.

Exit status: 0 if the new run has no regressions on either axis; 1 otherwise.
Use it as a CI gate between a baseline run and a candidate run.

Usage:
    python3 -m intergen.tests.compare_runs OLD NEW
    python3 -m intergen.tests.compare_runs OLD NEW --json report.json
where OLD/NEW are either a results.json file or a run_YYYYMMDD_HHMMSS directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from intergen.tests.capability_inventory import (
    capability_class, coverage_gaps, coverage_set, outcome_consistency,
)

# Grade severity, high-is-better, so a drop is new_rank < old_rank.
_GRADE_RANK = {"PASS": 2, "MIXED": 1, "FAIL": 0, "ERROR": 0}


def _load_results(path_str: str) -> dict:
    """Load a run's results.json from a file path or a run directory."""
    p = Path(path_str)
    if p.is_dir():
        p = p / "results.json"
    if not p.exists():
        raise FileNotFoundError(f"no results.json at {path_str!r} (looked at {p})")
    with open(p) as f:
        return json.load(f)


def _grade_map(run_data: dict) -> dict[str, str]:
    return {c.get("id", ""): c.get("grade", "FAIL")
            for c in run_data.get("conversations", [])}


def _rank(grade: str) -> int:
    return _GRADE_RANK.get(grade, 0)


def compare(old: dict, new: dict) -> dict:
    """Compute the full between-runs diff. Pure — no I/O, no exit."""
    g_old, g_new = _grade_map(old), _grade_map(new)

    grade_regressions = []   # in both, grade dropped
    grade_improvements = []  # in both, grade rose
    for cid in sorted(set(g_old) & set(g_new)):
        if _rank(g_new[cid]) < _rank(g_old[cid]):
            grade_regressions.append(
                {"id": cid, "from": g_old[cid], "to": g_new[cid]})
        elif _rank(g_new[cid]) > _rank(g_old[cid]):
            grade_improvements.append(
                {"id": cid, "from": g_old[cid], "to": g_new[cid]})

    dropped_convs = sorted(set(g_old) - set(g_new))   # conversation removed
    added_convs = sorted(set(g_new) - set(g_old))     # conversation new

    cov_old, cov_new = coverage_set(old), coverage_set(new)
    vanished = sorted(cov_old - cov_new)   # (capability, outcome, conv_id) cells lost
    gained = sorted(cov_new - cov_old)

    # A vanished cell whose conversation still exists = a conversation that
    # stopped exercising a (capability, outcome) (the subtle erosion); separate it
    # from a cell lost because the whole conversation was removed.
    new_ids = set(g_new)
    vanished_capability_only = [
        {"capability": cap, "outcome": outcome, "id": cid}
        for cap, outcome, cid in vanished if cid in new_ids
    ]
    vanished_from_removed = [
        {"capability": cap, "outcome": outcome, "id": cid}
        for cap, outcome, cid in vanished if cid not in new_ids
    ]

    gaps_new = coverage_gaps(new)
    gaps_old = coverage_gaps(old)

    # (capability, outcome) pairs that are a gap now but were NOT before = the
    # loudest outcome-level erosion (e.g. a deny cell removed so the gate branch
    # is no longer covered).
    def _missing_pairs(gaps: dict) -> set[tuple[str, str]]:
        # Only the CHASE-these gaps count as newly-missing signal: a gated tool's
        # corpus-required outcomes, and a read tool's executed outcomes (all
        # corpus-viable). Annotated non-required outcomes are not a regression signal.
        pairs = set()
        for tool, info in gaps["gated"].items():
            for o in info["required_missing"]:
                pairs.add((tool, o))
        for tool, info in gaps["read"].items():
            for o in info["missing_outcomes"]:
                pairs.add((tool, o))
        return pairs

    newly_missing = sorted(_missing_pairs(gaps_new) - _missing_pairs(gaps_old))

    # Falsifiability: a declared outcome tag that contradicts what the candidate run
    # actually did is a real defect (an authoritative tag lying about reality), so it
    # fails the verdict alongside grade/coverage regressions.
    inconsistencies = outcome_consistency(new)

    regression = bool(grade_regressions or vanished or dropped_convs
                      or inconsistencies)

    return {
        "regression": regression,
        "grade_regressions": grade_regressions,
        "outcome_inconsistencies": inconsistencies,
        "grade_improvements": grade_improvements,
        "dropped_conversations": dropped_convs,
        "added_conversations": added_convs,
        "vanished_cells": [{"capability": c, "outcome": o, "id": i}
                           for c, o, i in vanished],
        "vanished_capability_only": vanished_capability_only,
        "vanished_from_removed_conversations": vanished_from_removed,
        "gained_cells": [{"capability": c, "outcome": o, "id": i}
                         for c, o, i in gained],
        "coverage_gaps_new": gaps_new,
        "newly_missing_outcomes": [{"capability": c, "outcome": o}
                                   for c, o in newly_missing],
        "counts": {
            "old_conversations": len(g_old),
            "new_conversations": len(g_new),
            "old_cells": len(cov_old),
            "new_cells": len(cov_new),
        },
    }


def format_report(diff: dict, old_id: str, new_id: str) -> str:
    lines = []
    lines.append("Eval-harness run comparison")
    lines.append("=" * 60)
    lines.append(f"  baseline: {old_id}")
    lines.append(f"  candidate: {new_id}")
    c = diff["counts"]
    lines.append(f"  conversations: {c['old_conversations']} -> "
                 f"{c['new_conversations']}    "
                 f"coverage cells: {c['old_cells']} -> {c['new_cells']}")
    lines.append("")

    verdict = "REGRESSION" if diff["regression"] else "CLEAN"
    lines.append(f"VERDICT: {verdict}")
    lines.append("")

    def _section(title, items, render):
        if items:
            lines.append(f"{title} ({len(items)}):")
            for it in items:
                lines.append(f"  {render(it)}")
            lines.append("")

    _section("GRADE REGRESSIONS (hard)", diff["grade_regressions"],
             lambda r: f"{r['id']}: {r['from']} -> {r['to']}")
    _section("OUTCOME INCONSISTENCIES (declared tag contradicts the run)",
             diff["outcome_inconsistencies"],
             lambda v: f"{v['id']}: {v['declared']} — {v['reason']}")
    _section("DROPPED CONVERSATIONS (coverage loss)",
             diff["dropped_conversations"], lambda i: i)
    _section("VANISHED CELLS — (capability, outcome) no longer exercised by an "
             "existing conversation (coverage erosion)",
             diff["vanished_capability_only"],
             lambda v: f"{v['capability']} / {v['outcome']}  ({v['id']})")
    _section("NEWLY-MISSING OUTCOMES (a capability+outcome covered before, now a gap)",
             diff["newly_missing_outcomes"],
             lambda g: f"{g['capability']} / {g['outcome']}  "
                       f"[{capability_class(g['capability'])}]")

    # Coverage gaps in the new run (visible even if not a between-runs regression).
    # Outcome-granular: a gated tool is INCOMPLETE until every gate branch has a cell,
    # so write_file/run_command read "teaching-covered" but still surface their pending
    # deny/timeout/reject branches here.
    gaps = diff["coverage_gaps_new"]
    if gaps["gated"] or gaps["read"]:
        lines.append("COVERAGE GAPS in candidate (missing outcome cells):")
        for tool in sorted(gaps["gated"]):
            info = gaps["gated"][tool]
            notes = info.get("notes", {})
            # Chase-these = the corpus-REQUIRED outcomes still missing; the rest are
            # annotated (covered-elsewhere / not-corpus-viable), reported not chased,
            # never silently dropped. corpus_complete = required + teaching all present.
            chase = info["required_missing"]
            teach = "" if info["teaching_covered"] else "  +no teaching cell"
            # CORPUS-COMPLETE is corpus-scoped, not "fully tested": note the annotated
            # count so a reader never reads it as covering the not-corpus-viable
            # outcomes (the annotations are listed below it). (WC verify-don't-mask.)
            if info["corpus_complete"]:
                status = (f"CORPUS-COMPLETE ({len(info['notes'])} outcome(s) "
                          "annotated not-corpus-viable, see below)")
            else:
                status = "missing " + (", ".join(chase) or "(none required)")
            lines.append(f"  gated {tool}: {status}{teach}")
            for o, note in notes.items():
                lines.append(f"      - {o}: ANNOTATED — {note}")
        for tool in sorted(gaps["read"]):
            miss = ", ".join(gaps["read"][tool]["missing_outcomes"])
            lines.append(f"  read  {tool}: missing {miss}")
        lines.append("")

    _section("Improvements", diff["grade_improvements"],
             lambda r: f"{r['id']}: {r['from']} -> {r['to']}")
    _section("Added conversations", diff["added_conversations"], lambda i: i)

    if not diff["regression"]:
        lines.append("No grade regressions and no coverage erosion. "
                     "Candidate is clean against the baseline.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare two eval-harness runs for grade + coverage "
                    "regressions.")
    parser.add_argument("old", help="baseline results.json or run directory")
    parser.add_argument("new", help="candidate results.json or run directory")
    parser.add_argument("--json", metavar="PATH", default=None,
                        help="also write the structured diff to PATH")
    args = parser.parse_args(argv)

    old = _load_results(args.old)
    new = _load_results(args.new)
    diff = compare(old, new)

    report = format_report(diff, old.get("run_id", args.old),
                           new.get("run_id", args.new))
    print(report)

    if args.json:
        with open(args.json, "w") as f:
            json.dump(diff, f, indent=2)
        print(f"\nStructured diff written to: {args.json}")

    return 1 if diff["regression"] else 0


if __name__ == "__main__":
    sys.exit(main())
