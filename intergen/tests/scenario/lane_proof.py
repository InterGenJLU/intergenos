# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Run the graded corpus against ONE tree and say whether that tree regressed it.

WHY THIS EXISTS. The harness could grade a battery and it could drive the live
daemon across postures (``live_run``), but there was no single command a piece of
InterGen work could run to answer the one question its proof has to answer: does
the assistant still do, after this change, what it did before it? Without that
command the battery was something a person remembered to run, and the measured
result of relying on memory is that it does not get run.

WHAT IT DOES. Loads the corpus, drives every selected scenario against the tree
it is pointed at, streams one JSON row per scenario as that scenario finishes,
then writes the harness's own ``results.json`` + ``summary.txt`` through
:func:`report.write_run` so the artifacts are the same ones every other run
produces.

WHAT MAKES IT FAIL, and why those and not others:

  * **A scenario that could not be DRIVEN is an instrument failure** (exit 3).
    The daemon refused, the transport broke, the scenario raised. Nothing was
    measured, so nothing may be claimed. Measured 2026-08-26: a direct-mode run
    returned 64 ERROR results in 0.0s each from one broken reset call, and the
    output read like 64 product failures until someone opened it.
  * **A scenario that PASSED in the baseline and does not pass now is a
    regression** (exit 2), when ``--baseline`` names a prior ``results.json``.
  * **A grade that is simply not PASS is DATA, never a failure of this command.**
    The corpus deliberately holds scenarios the assistant does not satisfy yet —
    that is what a red-first battery is for. A runner that exited non-zero on
    them would push its callers to trim the corpus to whatever is green, which
    is the corpus deleting its own reason to exist.

THE TREE UNDER TEST IS NAMED, NEVER ASSUMED. The module prints the directory the
``intergen`` package actually resolved to and refuses a package outside the
current tree unless ``--allow-installed`` says that was the intent. Running a
harness script by absolute path once put the script's own directory first on
``sys.path`` and imported the INSTALLED package: the run described the shipped
release while its report claimed to describe the working tree.

Usage, from the root of the tree under test:

    python3 -m intergen.tests.scenario.lane_proof \\
        --out ./lane-proof-runs --run-id <lane-name> \\
        [--batch field_shapes] [--tag shape:S1] [--limit N] \\
        [--mode direct|dbus] [--baseline <prior results.json>]

Exit codes: 0 = every selected scenario was driven and none regressed.
            2 = at least one scenario that passed in the baseline no longer does.
            3 = at least one scenario could not be driven at all.
            4 = the selection was empty, or the tree under test is not the one asked for.
            5 = the run ABORTED: too many consecutive turns could not be driven, so
                the corpus was not measured and the artifacts say which part was not.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from intergen.tests.scenario import report
from intergen.tests.scenario import runner as _runner
from intergen.tests.scenario.schema import POSTURES
from intergen.tests.scenario.transport import ScenarioUndriveable

# The corpus that ships with the harness — the graded battery a lane must not
# regress. seeds/ is the smaller cross-posture set live_run drives; it is not
# this command's default because a lane's question is about the graded corpus.
_CORPUS_DIR = Path(__file__).resolve().parent / "corpus"


def _load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Read a JSONL capture. A malformed line is loud: a half-read trace would
    drop grounding signal and turn a real failure into a masked pass."""
    rows: list[dict[str, Any]] = []
    for i, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}:{i + 1}: invalid JSONL row: {e}") from e
    return rows


def _resolve_tree(allow_installed: bool) -> Path:
    """Print where ``intergen`` came from, and refuse a surprise."""
    import intergen
    pkg_dir = Path(intergen.__file__).resolve().parent
    tree = pkg_dir.parent
    print(f"### tree under test: {pkg_dir}", flush=True)
    cwd = Path.cwd().resolve()
    if tree != cwd and not allow_installed:
        print(f"### REFUSING: intergen resolved to {tree}, not the current tree "
              f"{cwd}. Run this from the root of the tree under test, or pass "
              f"--allow-installed if measuring the installed package was the "
              f"intent.", flush=True)
        raise SystemExit(4)
    return tree


def select(scenarios: list[Any], batches: list[str], tags: list[str],
           limit: int, *, posture: str) -> tuple[list[Any], list[Any]]:
    """The scenarios a run covers, and the ones this tier does not apply to.

    Every filter must match (AND), not any. Returns ``(selected, skipped)``:
    ``skipped`` is the batch/tag-matching scenarios that do NOT declare
    ``posture``, returned rather than dropped so the run can NAME them.

    POSTURE IS A FILTER, NOT ONLY A GRADING ARGUMENT. It used to be neither
    here: selection read batch, tag and limit, and every selected scenario was
    then graded under the run's posture. A scenario declaring ``["2B-locked"]``
    was therefore driven under ``--posture 35B-native``, where a correct
    top-tier answer fails a locked-floor expectation — the run reported product
    failures for scenarios that were never written for the box it ran on.
    ``live_run`` already had the rule and this uses that same function, so the
    two runners cannot drift into two answers to one question.

    ORDER: batch, then tag, then POSTURE, then limit. Limiting before the
    posture filter would take N scenarios and then discard some of them, so a
    ``--limit 20`` run would measure fewer than twenty and say nothing about it.
    """
    from intergen.tests.scenario.live_run import scenarios_for_posture
    out = list(scenarios)
    for b in batches:
        out = [s for s in out if f"batch:{b}" in s.tags]
    for t in tags:
        out = [s for s in out if t in s.tags]
    applicable = scenarios_for_posture(out, posture)
    applicable_ids = {id(s) for s in applicable}
    skipped = [s for s in out if id(s) not in applicable_ids]
    return (applicable[:limit] if limit else applicable), skipped


def failed_assertions(run: Any) -> list[dict[str, Any]]:
    """Every assertion this scenario did not satisfy, turn by turn.

    Kept small on purpose: the assertion's type, the value it was checking, why
    it exists, and a bounded slice of what was actually observed. The full
    record is results.json; this is what a row has to carry so a run read while
    it is still going says WHAT failed and not merely THAT something did.
    """
    out: list[dict[str, Any]] = []
    for ti, turn in enumerate(getattr(run.grade, "turns", []) or [], 1):
        for ar in getattr(turn, "results", []) or []:
            if getattr(ar, "passed", True):
                continue
            out.append({
                "turn": ti,
                "type": getattr(ar, "type", ""),
                "value": getattr(ar, "value", ""),
                "why": getattr(ar, "description", ""),
                "actual": (getattr(ar, "actual", "") or "")[:200],
            })
    return out


#: How many CONSECUTIVE undriveable turns end the run. Two, not one: a single refused
#: turn can be a restart or a blip, and a harness that aborts on one gets its abort
#: switched off, which is how the defect it guards against comes back. Two in a row is
#: not a blip — on the measured 2026-08-26 outage every turn after the engine died was
#: refused, so two costs one extra scenario and stops the other hour.
ABORT_AFTER_CONSECUTIVE_UNDRIVEABLE = 2


@dataclass
class DriveOutcome:
    """What a run of scenarios actually did — the three outcomes kept apart.

    ``graded`` are scenarios that were driven and produced a verdict. ``undriveable``
    are scenarios abandoned because a turn measured nothing; they carry NO grade and
    must never be counted among passes or failures. ``errored`` are scenarios that
    raised for some other reason — the product misbehaving is a finding, not an
    environment fault, and folding the two together is what let a dead engine read as
    an ordinary run.
    """

    graded: list = field(default_factory=list)
    undriveable: list[tuple[str, str]] = field(default_factory=list)
    errored: list[tuple[str, str]] = field(default_factory=list)
    consecutive_undriveable: int = 0
    aborted: bool = False
    abort_reason: str = ""
    not_attempted: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        # 5, NOT 4. Four already means "the selection was empty, or the tree under test
        # is not the one asked for" — two conditions a caller reacts to completely
        # differently from a dead engine. Reusing it would have made an aborted run
        # indistinguishable from a mis-typed selector to anything reading the code.
        if self.aborted:
            return 5
        if self.undriveable or self.errored:
            return 3
        return 0


def drive_scenarios(scenarios, transport, *, out_dir, run_id,
                    trace_lookup=None, posture=None, stream_path=None,
                    echo=False) -> DriveOutcome:
    """Drive a list of scenarios, keeping graded / undriveable / errored apart.

    Extracted from ``main`` so it can be driven with no daemon, no bus and no model.
    That it could NOT be before is not incidental: the run loop was the only place the
    "could not be driven" bookkeeping lived, and code that cannot be tested without a
    live engine is exactly the code that was wrong about a dead one.

    Writes results.json and summary.txt through ``report.write_run`` before returning,
    including on an abort — a run that stopped early must still leave the artifacts
    that say what it measured and why it stopped.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    outcome = DriveOutcome()
    driven: list = []
    stream = Path(stream_path) if stream_path else out / "stream.jsonl"

    with stream.open("w", encoding="utf-8") as fh:
        for i, sc in enumerate(scenarios, 1):
            if outcome.aborted:
                outcome.not_attempted.append(sc.id)
                continue
            s0 = time.monotonic()
            grade, detail, failed, kind = "ERROR", "", [], "errored"
            try:
                # Resolved through the module on EVERY call, deliberately — see the
                # import note above. Binding the name at import time silently detaches
                # this call site from anything that patches it.
                res = _runner.run_scenario(sc, transport, trace_lookup=trace_lookup,
                                           posture=posture)
                driven.append(res)
                outcome.graded.append(res)
                grade = res.grade.grade
                failed = failed_assertions(res)
                kind = "graded"
                outcome.consecutive_undriveable = 0
            except ScenarioUndriveable as exc:
                kind = "undriveable"
                grade = "UNDRIVEABLE"
                detail = exc.reason
                outcome.undriveable.append((sc.id, exc.reason))
                outcome.consecutive_undriveable += 1
                if (outcome.consecutive_undriveable
                        >= ABORT_AFTER_CONSECUTIVE_UNDRIVEABLE):
                    outcome.aborted = True
                    outcome.abort_reason = (
                        f"{outcome.consecutive_undriveable} consecutive turns could "
                        f"not be driven; the last said: {exc.reason}")
            except Exception as exc:      # noqa: BLE001 — an error IS a result
                outcome.errored.append((sc.id, f"{type(exc).__name__}: {exc}"))
                detail = f"{type(exc).__name__}: {exc}"
                outcome.consecutive_undriveable = 0
            dt = time.monotonic() - s0
            fh.write(json.dumps({"id": sc.id, "grade": grade, "kind": kind,
                                 "seconds": round(dt, 1), "tags": sc.tags,
                                 "error": detail, "failed": failed}) + "\n")
            fh.flush()
            if echo:
                print(f"[{i:>4}/{len(scenarios)}] {sc.id:<38} {grade:<12} {dt:6.1f}s"
                      + (f"  {len(failed)} failed assertion(s)" if failed else "")
                      + (f"  {detail}" if detail else ""), flush=True)

    report.write_run(driven, list(scenarios), out, run_id,
                     undriveable=outcome.undriveable,
                     abort_reason=outcome.abort_reason,
                     not_attempted=outcome.not_attempted)
    return outcome


def baseline_passes(path: str | Path) -> set[str]:
    """The scenario ids that PASSED in a prior ``results.json``."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {s["id"] for s in data.get("scenarios", []) if s.get("grade") == "PASS"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="intergen.tests.scenario.lane_proof",
        description="Drive the graded corpus against this tree and report "
                    "whether it regressed.")
    ap.add_argument("--out", default="./lane-proof-runs",
                    help="directory the run's artifacts are written under")
    ap.add_argument("--run-id", required=True,
                    help="names the run; the artifacts land in <out>/<run-id>/")
    ap.add_argument("--corpus", default=str(_CORPUS_DIR),
                    help="corpus file or directory (default: the shipped corpus)")
    ap.add_argument("--batch", action="append", default=[],
                    help="only scenarios tagged batch:<NAME> (repeatable)")
    ap.add_argument("--tag", action="append", default=[],
                    help="only scenarios carrying this exact tag (repeatable)")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N scenarios (0 = all of them)")
    ap.add_argument("--mode", choices=("direct", "dbus"), default="direct",
                    help="direct = an in-process daemon; dbus = the live one")
    ap.add_argument("--posture", required=True, choices=sorted(POSTURES),
                    help="the tier this run actually drives. REQUIRED: a "
                         "scenario turn can carry assertions written for "
                         "different tiers that contradict each other, so a run "
                         "that does not say which tier it drove grades some of "
                         "them against a box that was never there. There is no "
                         "default, because a default would be a guess about "
                         "the machine")
    ap.add_argument("--ready-timeout", type=float, default=300.0,
                    help="seconds to wait for the daemon to be able to serve")
    ap.add_argument("--baseline",
                    help="a prior results.json; a scenario that passed there "
                         "and does not pass now fails this run")
    ap.add_argument("--glass", default=None,
                    help="an always-on turn trace (glass.jsonl) to join into "
                         "grading; rows are matched to turns by trace id")
    ap.add_argument("--decisions", default=None,
                    help="a decisions.jsonl capture, when one was taken; it is "
                         "the only source that carries dispatch OUTCOMES, so "
                         "without it the outcome-dependent grounding "
                         "assertions fail closed (trace.OBSERVABILITY_GAPS)")
    ap.add_argument("--allow-installed", action="store_true",
                    help="permit an intergen resolved outside the current tree")
    args = ap.parse_args(argv)

    _resolve_tree(args.allow_installed)

    from intergen.tests.scenario import live_run
    from intergen.tests.scenario.loader import load_scenarios
    from intergen.tests.scenario.transport import ClientTransport

    scenarios, not_applicable = select(load_scenarios(args.corpus), args.batch,
                                       args.tag, args.limit,
                                       posture=args.posture)
    # NAMED, NEVER SILENT. A scenario left out because it targets another tier is
    # not a failure and not a pass — it is coverage this run does not have, and a
    # reader who is not told cannot tell the difference between "it passed" and
    # "it never ran". Printed with each one's DECLARED postures so the reason is
    # on the page rather than inferable.
    if not_applicable:
        print(f"### not applicable to {args.posture}: {len(not_applicable)} "
              f"scenario(s) not driven, because they declare other tiers",
              flush=True)
        for s in not_applicable:
            print(f"###   - {s.id} declares {', '.join(s.postures)}", flush=True)
    if not scenarios:
        if not_applicable:
            print(f"### REFUSING: every scenario the filters matched targets a "
                  f"tier other than {args.posture}, so this run would measure "
                  f"nothing. Name the posture these scenarios declare, or widen "
                  f"the filters.", flush=True)
            return 4
        print("### REFUSING: the selection is empty — a run that measures "
              "nothing must not report success.", flush=True)
        return 4
    turns = sum(len(s.turns) for s in scenarios)
    print(f"### selected: {len(scenarios)} scenarios / {turns} turns", flush=True)
    print(f"### posture: {args.posture} — only scenarios that DECLARE this tier "
          f"are driven, and assertions written for another tier do not apply",
          flush=True)

    out_dir = Path(args.out) / args.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    stream_path = out_dir / "scenarios.jsonl"

    # THE TRACE IS NOT OPTIONAL DECORATION. The grounding assertions
    # (no_fabricated_success, no_fabricated_state) are joined to the decision
    # trace and FAIL CLOSED without one, so a run that passes no lookup reddens
    # them on the absence of a trace rather than on anything the assistant did —
    # a measurement of the harness wearing the shape of a product failure. The
    # base lookup is built from the reply itself and always available; the
    # always-on glass file is layered in when it is there.
    glass_rows = None
    if args.glass:
        glass_path = Path(args.glass)
        if not glass_path.exists():
            print(f"### REFUSING: --glass {glass_path} does not exist. A named "
                  f"trace that is not there must not be silently dropped — the "
                  f"run would grade with the grounding assertions failing "
                  f"closed while its caller believed the trace was joined.",
                  flush=True)
            return 4
        glass_rows = _load_jsonl(glass_path)
        print(f"### trace: {len(glass_rows)} glass rows from {glass_path} at "
              f"start; the file is read as the run appends to it", flush=True)
    decisions_rows = None
    if args.decisions:
        decisions_path = Path(args.decisions)
        if not decisions_path.exists():
            print(f"### REFUSING: --decisions {decisions_path} does not exist.",
                  flush=True)
            return 4
        decisions_rows = _load_jsonl(decisions_path)
        print(f"### trace: {len(decisions_rows)} decision spans from "
              f"{decisions_path}", flush=True)
    else:
        print("### trace: no decisions capture — per-tool dispatch OUTCOMES "
              "are emitted nowhere else (trace.OBSERVABILITY_GAPS), so an "
              "assertion that needs one fails closed rather than guessing",
              flush=True)
    trace_lookup = live_run.build_trace_lookup(
        None, decisions_rows,
        glass_path=Path(args.glass) if args.glass else None)

    transport = ClientTransport(mode=args.mode)
    transport.await_ready(args.ready_timeout)
    print("### transport ready", flush=True)

    t0 = time.monotonic()
    # ONE run loop, shared with the harness's own self-tests. It used to live inline
    # here, which meant the "could not be driven" bookkeeping could only ever be
    # exercised against a live daemon — and code that cannot be tested without a live
    # engine is exactly the code that was wrong about a dead one (2026-08-26: four
    # scenarios graded PASS with no model behind them).
    outcome = drive_scenarios(
        scenarios, transport, out_dir=out_dir, run_id=args.run_id,
        trace_lookup=trace_lookup, posture=args.posture,
        stream_path=stream_path, echo=True)
    elapsed = time.monotonic() - t0
    print(f"### drove {len(outcome.graded)} of {len(scenarios)} scenarios in "
          f"{elapsed:.1f}s ({elapsed / max(1, turns):.1f}s per turn)", flush=True)

    results = report.build_results(outcome.graded, scenarios, args.run_id)
    print(f"### artifacts: {out_dir}/results.json, {out_dir}/summary.txt, "
          f"{stream_path}", flush=True)
    c = results["counts"]
    print(f"### {c['scenarios']} graded: {c['passed']} PASS  {c['mixed']} MIXED  "
          f"{c['failed']} FAIL  |  {len(outcome.undriveable)} could not be driven"
          f"  |  {len(outcome.errored)} errored", flush=True)

    if outcome.aborted:
        print(f"### ABORTED — {outcome.abort_reason}", flush=True)
        print(f"### {len(outcome.not_attempted)} scenario(s) were never attempted. "
              f"This run measured nothing about them and nothing about the "
              f"{len(outcome.undriveable)} it could not drive.", flush=True)
        return outcome.exit_code

    if outcome.undriveable or outcome.errored:
        print("### FAILED — these scenarios could not be driven, so this run "
              "measured nothing about them:", flush=True)
        for sid, why in outcome.undriveable + outcome.errored:
            print(f"###   {sid}: {why}", flush=True)
        return outcome.exit_code

    if args.baseline:
        was_passing = baseline_passes(args.baseline)
        now_passing = {s["id"] for s in results["scenarios"]
                       if s["grade"] == "PASS"}
        regressed = sorted(was_passing - now_passing)
        # A scenario the baseline does not contain is NOT a regression: a lane
        # that adds coverage would otherwise fail its own proof.
        regressed = [r for r in regressed
                     if r in {s["id"] for s in results["scenarios"]}]
        if regressed:
            print(f"### FAILED — {len(regressed)} scenario(s) passed in "
                  f"{args.baseline} and do not pass now:", flush=True)
            for sid in regressed:
                print(f"###   {sid}", flush=True)
            return 2
        print(f"### no regression against {args.baseline}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
