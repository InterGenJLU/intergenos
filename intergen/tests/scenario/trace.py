# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Trace join — the decision-trace view the grounding assertions read.

The structural grader (grader.py) grades a turn in two passes: a first pass over
the response text + dispatched tool names, then a TRACE pass that joins the
turn's decision trace by ``trace_id`` and resolves the grounding assertions
(``answer_consistent_with_tool``, ``no_fabricated_state``, ``dispatch_outcome``,
``no_fabricated_success``, …). Those assertions are not decidable from the prose
alone — they need to know whether the tool the answer leans on actually RAN and
SUCCEEDED. This module normalizes whatever trace source is available into one
``TraceView`` the grader consumes, so the grader has a single shape to read
regardless of transport.

What the daemon actually exposes today (measured, not assumed)
-------------------------------------------------------------
* The D-Bus ``Ask`` reply carries ``tool_calls`` (name + arguments), ``source``,
  ``used_llm``, ``trace_id`` and the delivered ``response`` — but NOT the tool
  results. ``_ask_direct`` parses the same reply, so ``tool_results`` is empty on
  BOTH transports; the ``TurnResult.tool_results`` field exists but the daemon
  never fills it.
* The always-on glass trace (``glass.jsonl``) threads ``route/decided``,
  ``delivery/final``, ``prompt/assembled`` and the ``decision/*`` verdicts by
  ``turn_id`` — but it does NOT emit per-tool dispatch outcomes or result
  content.
* The dev-gated decision trace (``decisions.jsonl`` under ``--observe`` /
  ``INTERGEN_TRACE``) carries the aggregate dispatch-outcome flags
  ``dispatch_any_failed`` / ``dispatch_any_denied`` / ``dispatch_any_blocked`` on
  the router span — the same signal the existing two-gate grader's trace pass
  already reads.

So the resolvable grounding signal today is: the reply (dispatched tool names +
their arguments + the delivered text + the route source) joined to the aggregate
dispatch-outcome flags from a decision-trace capture. Per-tool RESULT CONTENT is
observable NOWHERE — :data:`OBSERVABILITY_GAPS` records that as a telemetry
finding (the harness OBSERVES the gap; it does not patch the daemon). Where a
grounding assertion needs a signal the trace does not carry, the grader FAILS
CLOSED (an unverifiable grounding claim must never pass — verify, don't mask),
exactly as the existing grader's ``gate_action`` placeholder does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from intergen.tests.scenario.transport import TurnResult

# The nine shipped tools that read live system/web/fs state (Registry A,
# "reads live state? = yes"). A live-state answer must be backed by one of these
# on the same turn; a state answer produced with none dispatched is asserted from
# the model's prior — the fabrication class no_fabricated_state guards.
READS_REALITY_TOOLS: frozenset[str] = frozenset({
    "manage_packages", "manage_services", "run_command", "take_screenshot",
    "read_file", "analyze_file", "web_search",
})

# The eight gate outcomes from Registry C. The three a decision trace can attest
# per turn (via the aggregate router-span flags) are the failed / denied /
# blocked family; a clean dispatch with none of those set is executed_success.
DISPATCH_OUTCOMES: frozenset[str] = frozenset({
    "executed_success", "executed_fail", "deny", "blocked",
})

# Telemetry the grounding assertions WOULD read if the daemon emitted it. These
# are observability findings (§1.1 "a capability the harness cannot observe is
# written down as an observability gap — a defect against the daemon's telemetry,
# to be closed by emitting the missing field"), surfaced by the harness, never
# silently worked around. Until they are closed, the named assertions fail closed
# on a live run that captures no richer trace.
OBSERVABILITY_GAPS: tuple[str, ...] = (
    "tool result CONTENT is not emitted to glass.jsonl or the Ask reply — "
    "tool_result_nonempty / tool_output_contains cannot resolve from a live "
    "capture (they fail closed until the daemon emits per-tool result content)",
    "per-tool dispatch OUTCOME is not emitted to the always-on glass trace — "
    "outcome resolution requires a --observe decisions.jsonl capture; without "
    "one, answer_consistent_with_tool / no_fabricated_state / dispatch_outcome "
    "fall back to fail-closed",
)


@dataclass
class ToolDispatch:
    """One tool dispatch as the trace attests it.

    ``arguments`` come from the reply's ``tool_calls`` (always present).
    ``content`` is the tool's result text — present only when a trace source
    carries it (none does today; see OBSERVABILITY_GAPS), so it is usually "".
    ``executed`` / ``success`` / ``blocked`` mirror ToolResult's fields; they are
    populated from a rich capture (the recorded seed fixtures) or left at their
    None-equivalent defaults when only the reply is available.
    """
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    content: str = ""
    executed: bool | None = None
    success: bool | None = None
    blocked: bool | None = None

    @property
    def outcome(self) -> str | None:
        """The Registry-C outcome for this dispatch, or None if unattested.

        blocked → "blocked"; refused-before-run (not executed, not success) →
        "deny"; executed-but-errored → "executed_fail"; executed + success →
        "executed_success". None when the outcome flags were never populated (the
        reply alone cannot tell us), so the grader fails closed rather than guess.
        """
        if self.blocked:
            return "blocked"
        if self.executed is None and self.success is None:
            return None
        if not self.executed and not self.success:
            return "deny"
        if self.executed and not self.success:
            return "executed_fail"
        if self.executed and self.success:
            return "executed_success"
        return None


@dataclass
class TraceView:
    """The normalized decision trace for one turn, joined by ``trace_id``.

    One shape the grader reads regardless of where the trace came from (a live
    reply, a glass capture, a decisions.jsonl capture, or a recorded fixture).
    The per-tool :class:`ToolDispatch` list carries what the reply always has
    (names + args) plus outcome/content when a richer source supplied it; the
    aggregate ``dispatch_any_*`` flags carry the coarse outcome signal a
    decision-trace capture attests. ``outcomes_resolved`` is the honest gate: it
    is True only when the trace actually attests dispatch outcomes, so the grader
    can distinguish "the tool ran and succeeded" from "we have no idea" and fail
    closed on the latter.
    """
    trace_id: str = ""
    route_source: str = ""
    delivered_text: str = ""
    dispatches: list[ToolDispatch] = field(default_factory=list)
    dispatch_any_failed: bool | None = None
    dispatch_any_denied: bool | None = None
    dispatch_any_blocked: bool | None = None
    # The decomposer's sub-request set for a compound turn (the decomposition
    # tree WP-2.4 asserts against), from the glass decision/decompose |
    # compound_route event's ``sub_queries``. Empty when the turn did not
    # decompose — an assertion that expects decomposition then fails, correctly.
    sub_queries: list[str] = field(default_factory=list)
    # Whether a source that CAN carry the decomposer's verdict was actually
    # joined for this turn. Empty ``sub_queries`` means two completely different
    # things and they must not be reported as one: the router did not split the
    # request (this flag True), or nothing was read that could have said either
    # way (this flag False). A whole-corpus run that supplied no glass source
    # once reported ten scenarios as "no decomposition observed" when four of
    # them did decompose — the flag exists so that report cannot be written
    # again.
    decomposition_source_joined: bool = False
    # Which tools EACH clause of a compound turn dispatched, keyed by the
    # decomposer's 1-based sub-query index, from the glass ``prompt``/``subquery``
    # rows the router writes inside its per-clause loop.
    #
    # WHY THIS IS NOT DERIVABLE FROM ``dispatches``. That list is flat: the
    # router extends one list with every clause's calls, so by the time a reader
    # sees it, "which half of the request did this serve" is unanswerable. A
    # compound turn was graded as served on the strength of a dispatch that
    # belonged to its OTHER clause. Ordering is not a substitute — a clause may
    # dispatch none, one or several, so position proves nothing.
    sub_query_tools: dict[int, list[str]] = field(default_factory=dict)
    # Whether a source carrying that per-clause attribution was actually joined.
    # The same distinction ``decomposition_source_joined`` draws, for the same
    # reason: "this clause dispatched nothing" and "nothing was read that could
    # say what any clause dispatched" are different facts, and a grade that
    # reports them as one is reporting a product defect it did not measure.
    subquery_attribution_joined: bool = False

    # ── the review-gate lifecycle (WP-3.4) ──
    # ``gate_held`` is True once a dispatch entered hold_for_review (the panel/WS
    # consent path showed it). ``gate_outcome`` is the terminal state it reached
    # (a GATE_OUTCOME) or "" when unresolved — a gate that was held but never
    # resolved is the liveness failure the gate_outcome assertion fails on.
    gate_held: bool = False
    gate_outcome: str = ""

    # ── queries the grounding assertions use ──

    @property
    def gate_resolved(self) -> bool:
        """True when a held gate reached a terminal state. A gate that was never
        held is trivially resolved (nothing to wait on); a held gate with no
        terminal outcome is NOT resolved — the liveness violation."""
        if not self.gate_held:
            return True
        return self.gate_outcome in {"allow", "deny", "timeout", "cancel"}

    @property
    def tools_called(self) -> list[str]:
        return [d.name for d in self.dispatches]

    @property
    def outcomes_resolved(self) -> bool:
        """True when the trace attests dispatch outcomes at all.

        Either the aggregate flags were populated (a decision-trace capture) or a
        per-tool dispatch carries an outcome (a rich fixture / future telemetry).
        When neither holds, an outcome-dependent grounding assertion must fail
        closed — an unverified outcome is not a passing outcome. The router sets
        the three flags together, so ANY of them being attested means the
        outcome signal is present; an unset flag then reads as False (that
        outcome condition did not occur), never as "unknown".
        """
        if any(f is not None for f in (self.dispatch_any_failed,
                                       self.dispatch_any_denied,
                                       self.dispatch_any_blocked)):
            return True
        return any(d.outcome is not None for d in self.dispatches)

    def dispatch(self, tool: str) -> ToolDispatch | None:
        """The (last) dispatch of ``tool`` on this turn, or None if it never ran."""
        found = [d for d in self.dispatches if d.name == tool]
        return found[-1] if found else None

    def dispatched(self, tool: str) -> bool:
        return any(d.name == tool for d in self.dispatches)

    def any_reads_reality_dispatched(self) -> bool:
        return any(d.name in READS_REALITY_TOOLS for d in self.dispatches)

    def outcome_for(self, tool: str) -> str | None:
        """The resolved outcome for ``tool``, or None when it cannot be attributed.

        Prefers the per-tool ToolDispatch outcome (exact). Falls back to the
        aggregate flags ONLY when ``tool`` is the sole dispatch on the turn — an
        aggregate flag cannot be attributed to a specific tool on a multi-tool
        turn, so that case returns None and the caller fails closed rather than
        blame the wrong tool.
        """
        d = self.dispatch(tool)
        if d is None:
            return None
        exact = d.outcome
        if exact is not None:
            return exact
        if len(self.dispatches) != 1:
            return None
        if not self.outcomes_resolved:
            return None
        if self.dispatch_any_blocked:
            return "blocked"
        if self.dispatch_any_denied:
            return "deny"
        if self.dispatch_any_failed:
            return "executed_fail"
        return "executed_success"

    def any_dispatch_not_ok(self) -> bool | None:
        """True if any dispatch failed/denied/blocked; None if unresolved.

        The signal ``no_fabricated_success`` fires on: a success claim after a
        dispatch that did not succeed. None (not False) when the trace does not
        attest outcomes, so the caller fails closed instead of reading unresolved
        as "everything was fine".
        """
        if not self.outcomes_resolved:
            return None
        if any((self.dispatch_any_failed, self.dispatch_any_denied,
                self.dispatch_any_blocked)):
            return True
        for d in self.dispatches:
            oc = d.outcome
            if oc in ("executed_fail", "deny", "blocked"):
                return True
        return False

    # ── builders (one per trace source) ──

    @classmethod
    def from_turn_result(cls, tr: TurnResult,
                         spans: list[dict[str, Any]] | None = None) -> "TraceView":
        """Build from a live reply, optionally enriched with decision spans.

        The reply always gives dispatched tool names + arguments + the delivered
        text + the route source. ``spans`` (a ``decisions.jsonl`` capture, when
        ``--observe`` was on) supplies the aggregate dispatch-outcome flags; the
        reply's ``tool_results`` is honored if the daemon ever starts filling it
        (it is empty today). Without spans and without tool_results, the outcome
        flags stay None and outcome-dependent assertions fail closed.
        """
        # Reply tool_calls: [{"name": ..., "arguments": {...}}]. Join the
        # (currently-empty) tool_results by name so per-tool outcome/content is
        # captured the moment the daemon starts emitting it — no client change.
        results_by_name: dict[str, dict[str, Any]] = {}
        for r in tr.tool_results or []:
            if isinstance(r, dict) and r.get("name"):
                results_by_name[r["name"]] = r
        dispatches: list[ToolDispatch] = []
        for tc in tr.tool_calls or []:
            if not isinstance(tc, dict):
                continue
            name = tc.get("name") or tc.get("tool") or tc.get("tool_name")
            if not name:
                continue
            res = results_by_name.get(name, {})
            dispatches.append(ToolDispatch(
                name=name,
                arguments=tc.get("arguments") or tc.get("args") or {},
                content=res.get("content", "") or "",
                executed=res.get("executed") if "executed" in res else None,
                success=res.get("success") if "success" in res else None,
                blocked=res.get("blocked") if "blocked" in res else None,
            ))
        view = cls(
            trace_id=tr.trace_id,
            route_source=tr.source,
            delivered_text=tr.text,
            dispatches=dispatches,
        )
        if spans:
            view._absorb_spans(spans)
        return view

    @classmethod
    def from_glass_rows(cls, rows: list[dict[str, Any]],
                        trace_id: str | None = None) -> "TraceView":
        """Build from glass.jsonl rows (the always-on primary trace source).

        Extracts the route source (``route/decided``), the delivered text
        (``delivery/final``) and dispatched tool names/args where a decision row
        carries them. Glass does NOT emit per-tool dispatch outcomes, so the
        outcome flags stay None and outcome-dependent assertions fail closed on a
        glass-only capture (see OBSERVABILITY_GAPS) — the honest state, not a
        masked pass.
        """
        if trace_id is not None:
            rows = [r for r in rows if r.get("turn_id") == trace_id]
        view = cls(trace_id=trace_id or "")
        for r in rows:
            phase, event = r.get("phase"), r.get("event")
            detail = r.get("detail") or {}
            if phase == "route" and event == "decided":
                view.route_source = detail.get("source", view.route_source)
            elif phase == "delivery" and event == "final":
                view.delivered_text = detail.get("text", view.delivered_text)
                view.route_source = detail.get("source", view.route_source)
            elif phase == "decision" and event in ("decompose", "compound_route"):
                # The row itself is the attestation: this turn's decomposer
                # verdict WAS read, whatever it says.
                view.decomposition_source_joined = True
                sq = detail.get("sub_queries")
                if isinstance(sq, list) and sq:
                    view.sub_queries = [str(s) for s in sq]
            elif phase == "prompt" and event == "subquery":
                # One row per clause, written inside the router's per-clause
                # loop. The presence of a `tools` key is the attestation: this
                # run READ what that clause dispatched, even when the answer is
                # "nothing". A row without the key is an older emission and must
                # not be mistaken for an empty dispatch list.
                if "tools" in detail:
                    view.subquery_attribution_joined = True
                    try:
                        idx = int(detail.get("index"))
                    except (TypeError, ValueError):
                        continue
                    tools = detail.get("tools")
                    view.sub_query_tools[idx] = (
                        [str(t) for t in tools] if isinstance(tools, list) else [])
        return view

    @classmethod
    def from_capture(cls, cap: dict[str, Any]) -> "TraceView":
        """Build from a recorded capture dict (the seed-fixture shape).

        A capture is one turn's ground truth as a real (reply + decision-trace)
        capture would carry it::

            {"trace_id","route_source","text",
             "tools":[{"name","arguments","content","executed","success","blocked"}],
             "dispatch":{"failed":bool,"denied":bool,"blocked":bool}}

        Only the fields a real capture attests are honored; a capture that omits
        the ``dispatch`` block leaves the outcome flags None (fail-closed), so a
        fixture cannot accidentally assert an outcome the daemon does not expose.
        """
        dispatches = []
        for t in cap.get("tools", []) or []:
            dispatches.append(ToolDispatch(
                name=t["name"],
                arguments=t.get("arguments", {}) or {},
                content=t.get("content", "") or "",
                executed=t.get("executed"),
                success=t.get("success"),
                blocked=t.get("blocked"),
            ))
        d = cap.get("dispatch") or {}
        gate = cap.get("gate") or {}
        return cls(
            trace_id=cap.get("trace_id", ""),
            route_source=cap.get("route_source", ""),
            delivered_text=cap.get("text", ""),
            dispatches=dispatches,
            dispatch_any_failed=d.get("failed"),
            dispatch_any_denied=d.get("denied"),
            dispatch_any_blocked=d.get("blocked"),
            sub_queries=[str(s) for s in (cap.get("sub_queries") or [])],
            # A capture that names the key attests the verdict; one that omits it
            # attests nothing, and the grader must be able to tell the two apart.
            decomposition_source_joined="sub_queries" in cap,
            # Per-clause dispatch attribution, same naming-the-key discipline:
            # {"1": ["manage_packages"], "2": []} in a recorded fixture.
            sub_query_tools={
                int(k): [str(t) for t in (v or [])]
                for k, v in (cap.get("sub_query_tools") or {}).items()
            },
            subquery_attribution_joined="sub_query_tools" in cap,
            gate_held=bool(gate.get("held", False)),
            gate_outcome=gate.get("outcome", "") or "",
        )

    def _absorb_spans(self, spans: list[dict[str, Any]]) -> None:
        """Fold decisions.jsonl router-span attributes into the aggregate flags.

        Mirrors the existing grader's trace pass: the flags live under each span's
        ``attributes``; a turn's value is the OR across spans. Only sets a flag
        when at least one span attests it, so absence stays None (unresolved), not
        a silent False.
        """
        def any_attr(key: str) -> bool | None:
            seen = [s.get("attributes", {}).get(key) for s in spans]
            seen = [v for v in seen if v is not None]
            return any(seen) if seen else None
        self.dispatch_any_failed = any_attr("dispatch_any_failed")
        self.dispatch_any_denied = any_attr("dispatch_any_denied")
        self.dispatch_any_blocked = any_attr("dispatch_any_blocked")
