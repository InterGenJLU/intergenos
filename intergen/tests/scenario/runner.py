# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""WP-2.2 — the scenario runner: drive a scenario end-to-end.

The schema, loader, transport, grader, and isolation modules each own one
concern; nothing yet *drives a whole scenario against a daemon*. This module is
that driver — the one place that turns a scripted Scenario into a graded run:

1. **Snapshot** the backing stores before the run (WP-2.1 isolation), so cleanup
   can be a delta and leaks are detectable.
2. **Reset** conversation state once at the scenario start (between-scenario
   isolation); within the scenario the session is HELD so context accumulates.
   When the FIRST turn declares a session boundary, that boundary is applied
   BEFORE the reset — a restart is strictly stronger isolation than a reset, and
   resetting into a daemon about to be bounced is what raced a live run into an
   abort (the reset landed in the daemon's teardown window).
3. **Apply the session boundary** each turn requests BEFORE the turn is sent:
   ``restart-before`` bounces the daemon (a durable fact must survive a real
   process lifecycle), ``new-session-before`` takes the lighter fresh-session
   boundary. A requested boundary that the transport cannot perform fails loud.
4. **Ask + grade** every turn, joining each response to its decision trace.
5. **Memory-write-gap check** — a store scenario (a producer on the memory axis)
   that left the durable facts store empty is a HARD failure: the memory
   subsystem silently wrote nothing (the dogfood 'zero facts despite a 14-message
   conversation' failure), so a green transcript would otherwise mask a
   broken store.
6. **Delta cleanup + leak detection**, honoring linked pairs: a producer leaves
   its artifacts alive (``cleanup=False``); the consumer that names it
   (``cleanup_for``) sweeps BOTH from the producer's cutoff, so nothing the
   forget flow missed survives, and no pre-existing user row is ever touched.

The runner talks to the daemon only through the :class:`ScenarioTransport`
interface, so it drives the live daemon and the no-daemon mock identically — the
harness observes behavior, it never reaches inside the daemon to change it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from intergen.tests.scenario import isolation as iso
from intergen.tests.scenario.grader import ScenarioGrade, grade_scenario
from intergen.tests.scenario.schema import Scenario
from intergen.tests.scenario.trace import TraceView
from intergen.tests.scenario.transport import (
    ScenarioTransport, ScenarioUndriveable, TransportRefused, TurnResult)

# A per-turn trace resolver: given a turn's transport result, return the joined
# decision trace (by trace_id) or None. None everywhere degrades the grounding
# assertions to fail-closed — never a blind pass.
TraceLookup = Callable[[TurnResult], "TraceView | None"]

# Axis a store→recall scenario declares; the memory-write-gap check keys on it.
MEMORY_AXIS = "memory_persistence"

_BOUNDARY_METHOD = {
    "restart-before": "restart_daemon",
    "new-session-before": "new_session",
}


@dataclass
class MemoryWriteGap:
    """Whether a store scenario actually wrote a durable fact.

    A producer on the memory axis exists to store a fact; if the durable facts
    store is unchanged after it runs, the store silently did nothing — a HARD
    failure that a text-only grade cannot see. ``checked`` is False when there
    was no DB to inspect (the check then makes no claim, rather than guessing).
    """
    scenario_id: str
    checked: bool = False
    wrote_fact: bool = False

    @property
    def is_gap(self) -> bool:
        return self.checked and not self.wrote_fact

    def render(self) -> str:
        if not self.checked:
            return "memory-write-gap: not checked (no memory DB to inspect)"
        if self.wrote_fact:
            return "memory-write-gap: a durable fact was written (ok)"
        return ("MEMORY WRITE GAP — a store scenario left the durable facts "
                "store empty; the memory subsystem wrote nothing")


@dataclass
class ScenarioRun:
    """Everything one scenario run produced — grade plus the isolation record."""
    scenario_id: str
    grade: ScenarioGrade
    turn_results: list[TurnResult] = field(default_factory=list)
    traces: list[TraceView | None] = field(default_factory=list)
    boundaries: list[str] = field(default_factory=list)
    pre_snapshot: iso.MemorySnapshot | None = None
    write_gap: MemoryWriteGap | None = None
    cleanup: iso.CleanupResult | None = None
    leaks: iso.LeakReport | None = None

    @property
    def passed(self) -> bool:
        return self.grade.passed and not (self.write_gap and self.write_gap.is_gap)


def _apply_boundary(marker: str | None, transport: ScenarioTransport) -> str | None:
    """Perform the session boundary a turn requests before it is sent.

    A ``restart-before`` also re-blocks on readiness (the transport's restart
    already does, but the interface guarantee is fail-closed either way). Returns
    the boundary applied, or None when the turn requested none.
    """
    if marker is None:
        return None
    method = getattr(transport, _BOUNDARY_METHOD[marker])
    method()
    if marker == "restart-before":
        transport.await_ready()
    return marker


def _is_producer(scenario: Scenario) -> bool:
    """A store scenario that intentionally leaves its fact alive for a consumer:
    cleanup disabled AND it exercises the memory axis."""
    return (scenario.cleanup is False) and (MEMORY_AXIS in scenario.axis)


def _check_write_gap(scenario: Scenario, pre: iso.MemorySnapshot | None,
                     db_path: str | None, artifact_dirs, clock) -> MemoryWriteGap:
    gap = MemoryWriteGap(scenario_id=scenario.id)
    if not _is_producer(scenario) or pre is None or not db_path:
        return gap  # nothing to check, or nothing to check it against
    post = iso.snapshot(db_path, artifact_dirs=artifact_dirs, cutoff=clock())
    gap.checked = True
    gap.wrote_fact = bool(iso.detect_leaks(pre, post).new_facts)
    return gap


def _aggregate_cleanup(snaps: list[iso.MemorySnapshot], artifact_dirs) -> iso.CleanupResult:
    """Delta-clean every snapshot's cutoff and sum the results — a consumer
    sweeps its own run AND each producer it took responsibility for."""
    agg = iso.CleanupResult()
    for s in snaps:
        r = iso.delta_cleanup(s, artifact_dirs=artifact_dirs)
        agg.deleted_facts += r.deleted_facts
        agg.deleted_sessions += r.deleted_sessions
        agg.deleted_files += r.deleted_files
        agg.residual_facts += r.residual_facts
        agg.residual_sessions += r.residual_sessions
        agg.residual_files += r.residual_files
    return agg


def _cleanup_and_leaks(scenario, pre, db_path, artifact_dirs, prior_snapshots):
    """Clean this scenario's delta (plus any producer it owns via cleanup_for),
    then leak-check against the EARLIEST relevant cutoff so a producer's residue
    is included. A producer (cleanup=False) is left alone — its consumer owns it.
    """
    if scenario.cleanup is False or pre is None or not db_path:
        return None, None
    snaps = [pre]
    for producer_id in scenario.cleanup_for:
        producer_snap = (prior_snapshots or {}).get(producer_id)
        if producer_snap is not None:
            snaps.append(producer_snap)
    cleanup = _aggregate_cleanup(snaps, artifact_dirs)
    earliest = min(snaps, key=lambda s: s.cutoff)
    post = iso.snapshot(db_path, artifact_dirs=artifact_dirs)
    leaks = iso.detect_leaks(earliest, post)
    return cleanup, leaks


def run_scenario(scenario: Scenario, transport: ScenarioTransport, *,
                 trace_lookup: TraceLookup | None = None,
                 artifact_dirs=None, clock: Callable[[], float] | None = None,
                 prior_snapshots: dict[str, iso.MemorySnapshot] | None = None,
                 manage_cleanup: bool = True,
                 posture: str | None = None) -> ScenarioRun:
    """Drive one scenario end-to-end and return its graded, isolated run.

    ``trace_lookup`` resolves each turn's decision trace (None → grounding
    assertions fail closed). ``clock`` is injectable for deterministic tests.
    ``prior_snapshots`` carries the pre-run snapshots of already-run producers so
    a consumer's cleanup_for can sweep them. Set ``manage_cleanup=False`` to let
    a run-set driver own cleanup centrally. ``posture`` (WP-4.1) selects which
    posture-gated assertions apply when grading (None = posture-agnostic).
    """
    clock = clock or time.time
    db_path = transport.memory_db_path()
    pre = iso.snapshot(db_path, artifact_dirs=artifact_dirs, cutoff=clock()) if db_path else None

    results: list[TurnResult] = []
    traces: list[TraceView | None] = []
    boundaries: list[str] = []

    # BOUNDARY BEFORE RESET when the first turn declares one.
    #
    # The scenario-start reset used to run unconditionally before the loop, so a
    # scenario whose FIRST turn carries restart-before issued a ResetConversation
    # into the daemon it was about to bounce — and, measured on a live run, into
    # the window where the daemon was already going away: the reset call came
    # back "recipient disconnected" and the run aborted rather than grade a
    # contaminated corpus. Correct behaviour, avoidable cause.
    #
    # A restart is STRICTLY STRONGER isolation than a conversation reset (the
    # process that held the conversation state is gone), so resetting first was
    # redundant as well as racy. Applying the boundary first and resetting after
    # gives the same isolation guarantee against a daemon that is settled and
    # serving. Turns after the first are unaffected: their boundary still applies
    # in the loop, in order.
    first_marker = scenario.turns[0].session_marker if scenario.turns else None
    if first_marker:
        applied = _apply_boundary(first_marker, transport)
        if applied:
            boundaries.append(applied)

    transport.reset()  # scenario-start isolation; session HELD across the turns

    for i, turn in enumerate(scenario.turns):
        if not (i == 0 and first_marker):
            applied = _apply_boundary(turn.session_marker, transport)
            if applied:
                boundaries.append(applied)
        # A TURN THAT MEASURED NOTHING MUST NOT BE GRADED. Two ways it can happen,
        # and both abandon the scenario rather than let it reach grade_scenario:
        #
        #   1. The transport says outright that it got no response.
        #   2. The transport answered, but the ENGINE behind it is not reachable and
        #      this turn did not use the model. That pair is the measured outage of
        #      2026-08-26: the daemon stayed up, every model call got connection
        #      refused, intergen/llm.py logged one line and returned nothing, and the
        #      router served a degraded reply that graded PASS four times over.
        #      Neither half is sufficient alone — a turn a deterministic route served
        #      legitimately does not use the model either, and an engine can be down
        #      while a run is doing nothing that needs it — so both are required.
        #
        # This is deliberately CONSERVATIVE: it can abandon a scenario whose answer
        # happened to be correct without the model. That trade is the right way round.
        # A withheld verdict costs a re-run; a verdict awarded by a harness that could
        # not reach the product is a false statement about the product, and it is the
        # kind that gets believed.
        try:
            res = transport.ask(turn.user)
        except TransportRefused as exc:
            raise ScenarioUndriveable(scenario.id, i, str(exc)) from exc
        if not res.used_llm:
            reachable, why = transport.engine_reachable()
            if not reachable:
                raise ScenarioUndriveable(
                    scenario.id, i,
                    f"the turn was answered without the model and the engine is "
                    f"unreachable, so nothing was measured: {why}")
        results.append(res)
        traces.append(trace_lookup(res) if trace_lookup else None)

    grade = grade_scenario(scenario, results, traces, posture=posture)
    run = ScenarioRun(scenario_id=scenario.id, grade=grade, turn_results=results,
                      traces=traces, boundaries=boundaries, pre_snapshot=pre)

    run.write_gap = _check_write_gap(scenario, pre, db_path, artifact_dirs, clock)
    if run.write_gap.is_gap:
        # A silent no-write is a HARD scenario failure; reflect it in the rollup
        # so the run's grade and .passed agree.
        run.grade.grade = "FAIL"

    if manage_cleanup:
        run.cleanup, run.leaks = _cleanup_and_leaks(
            scenario, pre, db_path, artifact_dirs, prior_snapshots)
    return run


def _order_for_linked_pairs(scenarios: list[Scenario]) -> list[Scenario]:
    """Order so every producer runs before the consumer that names it.

    cleanup_for points consumer → producer; a depth-first visit of a scenario's
    producers first yields producers-before-consumers, so a consumer's recall/
    forget always runs against an already-stored fact. A cleanup_for id absent
    from the set is ignored here (the loader already rejects a dangling one).
    """
    by_id = {s.id: s for s in scenarios}
    ordered: list[Scenario] = []
    seen: set[str] = set()

    def visit(s: Scenario) -> None:
        if s.id in seen:
            return
        seen.add(s.id)
        for producer_id in s.cleanup_for:
            producer = by_id.get(producer_id)
            if producer is not None:
                visit(producer)
        ordered.append(s)

    for s in scenarios:
        visit(s)
    return ordered


def run_scenarios(scenarios: list[Scenario], transport: ScenarioTransport, *,
                  trace_lookup: TraceLookup | None = None,
                  artifact_dirs=None,
                  clock: Callable[[], float] | None = None,
                  posture: str | None = None) -> list[ScenarioRun]:
    """Run a set of scenarios, honoring linked pairs.

    Producers are ordered before their consumers; each pre-run snapshot is
    threaded forward so a consumer's ``cleanup_for`` sweep reaches its producer's
    residue. ``posture`` (WP-4.1) is forwarded to every scenario so posture-gated
    assertions apply only under the matching tier (None = posture-agnostic); it is
    threaded here because ``run_scenario`` already takes it but the set-runner
    silently dropped it, which would grade a live per-posture battery
    posture-agnostically. Returns one :class:`ScenarioRun` per scenario, in
    execution order.
    """
    prior_snapshots: dict[str, iso.MemorySnapshot] = {}
    runs: list[ScenarioRun] = []
    for scenario in _order_for_linked_pairs(scenarios):
        run = run_scenario(scenario, transport, trace_lookup=trace_lookup,
                           artifact_dirs=artifact_dirs, clock=clock,
                           prior_snapshots=prior_snapshots, manage_cleanup=True,
                           posture=posture)
        if run.pre_snapshot is not None:
            prior_snapshots[scenario.id] = run.pre_snapshot
        runs.append(run)
    return runs
