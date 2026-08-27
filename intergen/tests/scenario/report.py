# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""WP-3.1 — run artifacts: serialize a scenario run to results.json + summary.txt.

A graded run has to leave a durable, self-describing record so the comparator can
diff two runs and a human can read one. Per the run-artifact contract (§6.1):

* ``results.json`` — one machine-readable record per run: every scenario's id,
  grade, axes, postures, declared capabilities, the per-turn TRANSCRIPT (glass
  ``turn_id`` + the question as sent + the reply as delivered) and assertion
  results (``observed`` recorded on every failure so a line is self-diagnosing),
  plus the isolation record (session boundaries applied, memory-write-gap
  verdict, cleanup and leak reports) and the per-axis pass-rate metrics the
  longitudinal trend reads.

  The transcript is what makes an audit read possible from the run dir ALONE. It
  was added after a graded-PASS turn whose delivered answer did not address its
  question could only be caught by joining the run back to the glass trace by
  hand — the artifacts recorded grades and gates but never a word of what was
  said. Fields are ADDITIONS; every pre-existing field keeps its name and
  meaning, so the comparator and every other consumer are unaffected.
* ``summary.txt`` — the same run rendered for a person: the verdict counts, the
  per-axis pass-rate table, and every non-PASS scenario named with its failing
  assertions.

The comparator (:mod:`.comparator`) consumes ``results.json``; nothing here
grades — it only serializes what the runner already produced.
"""

from __future__ import annotations

import json
from pathlib import Path

from intergen.tests.scenario.runner import ScenarioRun
from intergen.tests.scenario.schema import Scenario

# Ordered so the summary table is stable run to run.
_AXIS_ORDER = [
    "fabrication", "routing", "decomposer", "capability_recall",
    "context_persistence", "memory_persistence",
]


def _assertion_dict(r) -> dict:
    """Serialize one AssertionResult — observed is kept on every failure so the
    line is self-diagnosing (an empty observed on a pass is intentional)."""
    return {
        "type": r.type,
        "value": r.value,
        "passed": bool(r.passed),
        "gate": r.gate,
        "observed": "" if r.passed else (r.actual or r.description),
    }


def _turn_transcript(run: ScenarioRun, scenario: Scenario | None, i: int) -> dict:
    """The turn's transcript: the glass join key, the question as SENT, and the
    reply as DELIVERED.

    Without these three, an audit read of a graded turn requires joining the run
    back to the glass trace by hand — which is how a graded-PASS turn whose
    answer did not address its question stayed invisible in the artifacts. The
    run dir alone must be enough to reconstruct what was asked and what came
    back. Missing sources degrade to empty strings (a short run set or a
    scenario record that was not supplied), never to a raised exception mid-
    serialization.
    """
    res = run.turn_results[i] if i < len(run.turn_results) else None
    trace = run.traces[i] if i < len(run.traces) else None
    turn = scenario.turns[i] if scenario and i < len(scenario.turns) else None
    turn_id = (res.trace_id if res else "") or (trace.trace_id if trace else "")
    return {
        "turn_id": turn_id,
        "question": (turn.user if turn else "") or "",
        "reply": (res.text if res else "") or "",
    }


def _turn_dict(run: ScenarioRun, scenario: Scenario | None, i: int, tg) -> dict:
    """One turn's record — grade + gates + assertions + the transcript."""
    d = {"grade": tg.grade, "gate_a": tg.gate_a, "gate_b": tg.gate_b}
    d.update(_turn_transcript(run, scenario, i))
    d["assertions"] = [_assertion_dict(r) for r in tg.results]
    return d


def scenario_to_dict(run: ScenarioRun, scenario: Scenario | None) -> dict:
    """One scenario's full record — grade + metadata + per-turn assertions +
    the isolation record. ``scenario`` supplies the authoring metadata (axes,
    postures, capabilities); None degrades those to empty rather than raising."""
    wg = run.write_gap
    cl = run.cleanup
    lk = run.leaks
    return {
        "id": run.scenario_id,
        "name": scenario.name if scenario else "",
        "grade": run.grade.grade,
        "axis": list(scenario.axis) if scenario else [],
        "postures": list(scenario.postures) if scenario else [],
        "capabilities": list(scenario.capabilities) if scenario else [],
        "category": scenario.category if scenario else "",
        "boundaries": list(run.boundaries),
        "write_gap": (
            {"checked": wg.checked, "is_gap": wg.is_gap, "wrote_fact": wg.wrote_fact}
            if wg is not None else None),
        "cleanup": (
            {"deleted_facts": cl.deleted_facts, "deleted_sessions": cl.deleted_sessions,
             "deleted_files": cl.deleted_files, "incomplete": cl.incomplete}
            if cl is not None else None),
        "leaks": (
            {"leaked": lk.leaked, "new_facts": lk.new_facts,
             "new_sessions": lk.new_sessions, "new_files": lk.new_files}
            if lk is not None else None),
        "turns": [
            _turn_dict(run, scenario, i, tg)
            for i, tg in enumerate(run.grade.turns)
        ],
    }


def axis_metrics(scenario_dicts: list[dict]) -> dict[str, dict]:
    """Per-axis pass-rate over a run — the longitudinal trend's raw numbers.

    A scenario counts toward EVERY axis it declares (a fabrication+routing
    scenario is in both denominators), so the fabrication-guard pass-rate and the
    tool-grounding rate each get an independent number, exactly the per-axis trend
    §6.2 asks for. pass_rate is over scenarios that reached a grade; None when the
    axis has no scenarios (reported, never a silent 0/0).
    """
    metrics: dict[str, dict] = {}
    for s in scenario_dicts:
        for axis in s.get("axis", []):
            m = metrics.setdefault(
                axis, {"total": 0, "passed": 0, "mixed": 0, "failed": 0})
            m["total"] += 1
            g = s.get("grade", "FAIL")
            if g == "PASS":
                m["passed"] += 1
            elif g == "MIXED":
                m["mixed"] += 1
            else:
                m["failed"] += 1
    for m in metrics.values():
        m["pass_rate"] = round(m["passed"] / m["total"], 4) if m["total"] else None
    return metrics


def build_results(runs: list[ScenarioRun], scenarios: list[Scenario],
                  run_id: str, judge_annotations: dict | None = None, *,
                  undriveable: list[tuple[str, str]] | None = None,
                  abort_reason: str = "",
                  not_attempted: list[str] | None = None) -> dict:
    """The full results.json object for a run (pure — no I/O).

    ``judge_annotations`` (opt-in, WP-3.2) maps scenario id -> the serialized
    per-turn judge triage; when present, each scenario record carries a ``judge``
    field ALONGSIDE its structural grade — the grade is never altered by it.
    """
    by_id = {s.id: s for s in scenarios}
    scenario_dicts = [scenario_to_dict(r, by_id.get(r.scenario_id)) for r in runs]
    if judge_annotations:
        for s in scenario_dicts:
            if s["id"] in judge_annotations:
                s["judge"] = judge_annotations[s["id"]]
    grades = [s["grade"] for s in scenario_dicts]
    return {
        "run_id": run_id,
        "counts": {
            "scenarios": len(scenario_dicts),
            "passed": grades.count("PASS"),
            "mixed": grades.count("MIXED"),
            "failed": len(grades) - grades.count("PASS") - grades.count("MIXED"),
        },
        "scenarios": scenario_dicts,
        "axis_metrics": axis_metrics(scenario_dicts),
        # KEPT OUT OF "scenarios" AND OUT OF "counts" DELIBERATELY. A scenario that
        # could not be driven has no grade, so counting it anywhere among passes,
        # failures or mixed would be inventing one — which is the defect this record
        # exists to end. Readers that only know about "scenarios" therefore see a
        # SMALLER run, never a wrong one, and the fields below say what is missing.
        "undriveable": [{"id": sid, "reason": why}
                        for sid, why in (undriveable or [])],
        "abort_reason": abort_reason,
        "not_attempted": list(not_attempted or []),
    }


def format_summary(results: dict) -> str:
    """Render results.json for a human — verdict counts, the per-axis table, and
    every non-PASS scenario named with its failing assertions."""
    lines: list[str] = []
    c = results["counts"]
    lines.append(f"Scenario run {results.get('run_id', '')}")
    lines.append("=" * 60)
    lines.append(f"  {c['scenarios']} scenarios: {c['passed']} PASS  "
                 f"{c['mixed']} MIXED  {c['failed']} FAIL")
    # THE ABORT LEADS. A person opening summary.txt after a run that stopped early must
    # not have to notice that the scenario count is smaller than they expected; the
    # first thing under the counts says the run stopped and why.
    undriveable = results.get("undriveable") or []
    not_attempted = results.get("not_attempted") or []
    abort_reason = results.get("abort_reason") or ""
    if abort_reason:
        lines.append("")
        lines.append("*** RUN ABORTED — THIS RUN DID NOT MEASURE THE WHOLE CORPUS ***")
        lines.append(f"  reason: {abort_reason}")
        if not_attempted:
            lines.append(f"  {len(not_attempted)} scenario(s) never attempted: "
                         + ", ".join(not_attempted))
    if undriveable:
        lines.append("")
        lines.append(f"COULD NOT BE DRIVEN ({len(undriveable)}) — no verdict was "
                     f"reached for these, in either direction:")
        for u in undriveable:
            lines.append(f"  {u['id']}: {u['reason']}")
    lines.append("")
    lines.append("Per-axis pass-rate:")
    am = results.get("axis_metrics", {})
    ordered = [a for a in _AXIS_ORDER if a in am] + \
              sorted(a for a in am if a not in _AXIS_ORDER)
    for a in ordered:
        m = am[a]
        rate = "n/a" if m["pass_rate"] is None else f"{m['pass_rate'] * 100:.0f}%"
        lines.append(f"  {a:<22} {rate:>5}  ({m['passed']}/{m['total']})")
    lines.append("")
    non_pass = [s for s in results["scenarios"] if s["grade"] != "PASS"]
    if non_pass:
        lines.append(f"Non-PASS scenarios ({len(non_pass)}):")
        for s in non_pass:
            lines.append(f"  [{s['grade']}] {s['id']}")
            for ti, t in enumerate(s["turns"]):
                for a in t["assertions"]:
                    if not a["passed"]:
                        lines.append(f"      turn {ti + 1} {a['gate']}:{a['type']} "
                                     f"-> {a['observed']}")
            wg = s.get("write_gap")
            if wg and wg.get("is_gap"):
                lines.append("      MEMORY WRITE GAP (store left the facts store empty)")
    elif c["scenarios"] and not (undriveable or not_attempted or abort_reason):
        lines.append("All scenarios PASS.")
    elif c["scenarios"]:
        # SOMETHING PASSED, BUT NOT EVERYTHING WAS MEASURED, SO "ALL" IS A LIE.
        # Caught 2026-08-26 by reading a real artifact for the second time, on the live
        # engine-kill run: one scenario graded PASS, two could not be driven and one was
        # never attempted, and the summary still ended "All scenarios PASS." The first
        # correction only silenced that line when NOTHING was graded, which fixed the
        # empty corner and left this one — a run can abort with a passing scenario
        # behind it, and that is the report a reader is most likely to skim.
        #
        # The word doing the damage is "All". It is a claim about the SELECTED set, and
        # the selected set is larger than the graded set whenever anything was
        # undriveable or never attempted.
        missing = len(undriveable) + len(not_attempted)
        lines.append(
            f"Every scenario this run GRADED passed ({c['scenarios']}). That is not "
            f"the whole selection: {missing} more were selected and never reached a "
            f"verdict. Do not read this as a clean run.")
    else:
        # NO SCENARIO WAS GRADED, SO THERE IS NOTHING TO CALL PASSING. Caught by
        # reading a real artifact rather than by a test: an aborted run — zero graded,
        # two undriveable, two never attempted — ended its summary with "All scenarios
        # PASS.", because the old branch read "no non-PASS scenarios" as "everything
        # passed". An empty set satisfies that, which makes the emptiest possible run
        # produce the most reassuring possible sentence. That is the same false
        # all-clear, one file further out, as the four graded PASSES this cut exists
        # to stop.
        lines.append("NO SCENARIO WAS GRADED. This run reached no verdict about "
                     "anything — do not read the counts above as a pass.")
    return "\n".join(lines) + "\n"


def write_run(runs: list[ScenarioRun], scenarios: list[Scenario],
              out_dir: str | Path, run_id: str, *,
              undriveable: list[tuple[str, str]] | None = None,
              abort_reason: str = "",
              not_attempted: list[str] | None = None) -> dict:
    """Write results.json + summary.txt under ``out_dir`` and return the results
    dict. The directory is created if absent; ``run_id`` names the run.

    ``undriveable`` / ``abort_reason`` / ``not_attempted`` record what the run could
    NOT measure. They are written even when empty so their absence from an artifact is
    a fact about the run rather than a fact about which version wrote it."""
    results = build_results(runs, scenarios, run_id, undriveable=undriveable,
                            abort_reason=abort_reason, not_attempted=not_attempted)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    (out / "summary.txt").write_text(format_summary(results), encoding="utf-8")
    return results
