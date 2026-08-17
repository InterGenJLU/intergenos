# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Decision-path reconstruction from the InterGen decision trace.

Phase 1 of the quality arc (decision-trace correlation) has one exit criterion:
**one harness scenario's FULL decision path must be reconstructable from the
trace alone.** This module IS that reconstruction. Given the decision spans of a
single turn (all spans sharing one ``trace_id`` in ``decisions.jsonl``), it
reassembles the ordered six-element decision path the 2026-06-11 harness plan
specifies:

    1. input
    2. classification (+ why: query_type, semantic score, the top1-top2 gap)
    3. route choice (+ the alternatives considered — the scored tiers evaluated)
    4. tool calls (each: fired?, the provenance-gate verdict, the outcome)
    5. synthesis (its inputs — which tool result / grounding fed the answer)
    6. final output

so a reviewer — or a Gate-A assertion — can SEE the path rather than guess it
from the answer text (the harness plan's hard rule: *"never guess at routing
bugs — the trace must show it; if it doesn't, fix the trace"*).

Pure and dependency-free: :func:`reconstruct` consumes span dicts in the
``trace.Span.as_record()`` shape (``trace_id`` / ``span_id`` / ``parent_span_id``
/ ``seq`` / ``name`` / ``kind`` / ``attributes`` …); :func:`load_trace` reads
them from a ``decisions.jsonl`` written by a ``runner --observe`` run.

Span-name contract (emitted by the runtime — see router.py / tool_registry.py):
  * ``router.route``      (kind request) — the turn root; carries input/classify/
                          route-trail/output attributes.
  * ``router.llm_tools``  (kind llm)     — the tool-decision model call.
  * ``tool.execute``      (kind tool)    — one per tool invocation.
  * ``tool.gate``         (kind gate)    — the provenance verdict, child of a
                          ``tool.execute``.
  * ``llm.synth`` / ``router.llm_freeform`` (kind llm) — the synthesis call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# The canonical ordered element names — the reconstruction's contract with the
# grader/reviewer. Keep in step with the render() order and the harness plan.
ELEMENTS = (
    "input",
    "classification",
    "route",
    "tool_calls",
    "synthesis",
    "final_output",
)

_ROOT_NAME = "router.route"
_TOOL_NAME = "tool.execute"
_GATE_NAME = "tool.gate"
_SYNTH_NAMES = ("llm.synth", "router.llm_freeform")


def _attrs(span: dict[str, Any]) -> dict[str, Any]:
    return span.get("attributes", {}) or {}


@dataclass
class DecisionPath:
    """The reconstructed six-element decision path for one turn (one trace_id).

    Each element is a plain dict of the signals recovered from the spans (empty
    when the element left no span — e.g. ``tool_calls`` is empty on a freeform
    turn). ``synthesis`` is None when no synthesis call ran (a deterministic
    fast-path answer). Presence, not truthiness, is what
    :meth:`elements_present` reports — an element backed by a real span counts
    even if some of its optional content (raw text) was gated out.
    """

    trace_id: str
    input: dict[str, Any] = field(default_factory=dict)
    classification: dict[str, Any] = field(default_factory=dict)
    route: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    synthesis: dict[str, Any] | None = None
    final_output: dict[str, Any] = field(default_factory=dict)
    span_count: int = 0
    _present: dict[str, bool] = field(default_factory=dict, repr=False)

    def elements_present(self) -> dict[str, bool]:
        """Which of the six elements the trace actually carried."""
        return dict(self._present)

    def missing_elements(self) -> list[str]:
        return [e for e in ELEMENTS if not self._present.get(e, False)]

    def is_complete(self, *, require_tools: bool = False,
                    require_synthesis: bool = False) -> bool:
        """True when the mandatory-for-every-turn elements are present.

        input / classification / route / final_output are present on EVERY
        traced turn. tool_calls and synthesis are path-dependent: a tool-firing
        scenario (the Phase-1 exit scenario, "list the printers") requires both,
        so its proving test passes ``require_tools=require_synthesis=True``; a
        deterministic fast-path turn legitimately has neither.
        """
        base = all(self._present.get(e, False)
                   for e in ("input", "classification", "route", "final_output"))
        if require_tools and not self._present.get("tool_calls", False):
            return False
        if require_synthesis and not self._present.get("synthesis", False):
            return False
        return base

    def render(self) -> str:
        """A human-readable rendering of the decision path (the reviewer's view
        — the path the harness plan's Verification #1 requires be visible)."""
        L: list[str] = []
        L.append(f"DECISION PATH  trace_id={self.trace_id}  "
                 f"({self.span_count} spans)")
        # 1 input
        i = self.input
        itxt = f' "{i["text"]}"' if i.get("text") is not None else ""
        L.append(f"  1. input        : {i.get('chars', '?')} chars{itxt}")
        # 2 classification
        c = self.classification
        L.append(f"  2. classify     : query_type={c.get('query_type')!r} "
                 f"semantic_score={c.get('semantic_score')} "
                 f"gap={c.get('semantic_gap')} "
                 f"intent={c.get('semantic_intent_id')!r}")
        # 3 route + alternatives
        r = self.route
        L.append(f"  3. route        : WON={r.get('routed_via')!r}")
        for step in r.get("alternatives", []):
            extra = {k: v for k, v in step.items()
                     if k not in ("stage", "outcome")}
            L.append(f"       - {step.get('stage'):<12} {step.get('outcome'):<9} "
                     f"{extra if extra else ''}")
        # 4 tool calls
        if self.tool_calls:
            L.append(f"  4. tools        : {len(self.tool_calls)} call(s)")
            for t in self.tool_calls:
                g = t.get("gate", {})
                L.append(f"       - {t.get('tool_name')!r} "
                         f"gate={g.get('action')!r} "
                         f"executed={t.get('executed')} success={t.get('success')} "
                         f"counterfactual={t.get('counterfactual')!r}")
        else:
            L.append("  4. tools        : (none fired)")
        # 5 synthesis
        s = self.synthesis
        if s is not None:
            L.append(f"  5. synthesis    : via={s.get('via')!r} "
                     f"inputs={ {k: v for k, v in s.items() if k != 'via'} }")
        else:
            L.append("  5. synthesis    : (deterministic — no model synthesis)")
        # 6 output
        o = self.final_output
        otxt = f' "{o["text"]}"' if o.get("text") is not None else ""
        L.append(f"  6. output       : source={o.get('source')!r} "
                 f"handled={o.get('handled')} used_llm={o.get('used_llm')} "
                 f"{o.get('chars', '?')} chars{otxt}")
        missing = self.missing_elements()
        L.append(f"  complete={not missing}"
                 + (f"  missing={missing}" if missing else ""))
        return "\n".join(L)


def _counterfactual(tool: dict[str, Any], gate: dict[str, Any]) -> str:
    """A compact 'should-have / shouldn't-have-fired' read for one tool call,
    from the gate verdict + execution outcome already on the spans.

    * ``fired``           — gate said execute and the tool ran.
    * ``blocked_by_gate`` — the gate refused/held (reject|hold_for_review): the
                            call was PROPOSED but SHOULD-NOT (or not-yet) fire.
    * ``safety_blocked``  — a BLOCKED-tier refusal (never narrated as success).
    * ``failed``          — allowed and ran but the tool itself failed.
    """
    if tool.get("blocked"):
        return "safety_blocked"
    action = gate.get("action")
    if action in ("reject", "hold_for_review"):
        return "blocked_by_gate"
    if tool.get("executed") and not tool.get("success"):
        return "failed"
    return "fired"


def reconstruct(spans: Iterable[dict[str, Any]]) -> DecisionPath:
    """Reassemble the six-element decision path from one turn's spans.

    ``spans`` are all records sharing a single ``trace_id``. Ordering by ``seq``
    gives the deterministic decision order even for spans born in the same
    millisecond. Missing spans degrade gracefully — the corresponding element is
    simply marked absent (see :meth:`DecisionPath.elements_present`).
    """
    spans = sorted(spans, key=lambda s: s.get("seq", 0))
    trace_id = spans[0].get("trace_id", "") if spans else ""
    by_id = {s.get("span_id"): s for s in spans}
    root = next((s for s in spans if s.get("name") == _ROOT_NAME), None)
    path = DecisionPath(trace_id=trace_id, span_count=len(spans))
    present: dict[str, bool] = {e: False for e in ELEMENTS}

    if root is not None:
        ra = _attrs(root)
        # 1. input
        path.input = {"chars": ra.get("input_chars")}
        if "input_text" in ra:
            path.input["text"] = ra["input_text"]
        present["input"] = "input_chars" in ra
        # 2. classification (+ why)
        path.classification = {
            "query_type": ra.get("query_type"),
            "semantic_score": ra.get("semantic_score"),
            "semantic_runner_up": ra.get("semantic_runner_up"),
            "semantic_gap": ra.get("semantic_gap"),
            "semantic_intent_id": ra.get("semantic_intent_id"),
            "needs_decomposition": ra.get("needs_decomposition"),
        }
        present["classification"] = "query_type" in ra
        # 3. route choice + alternatives considered
        path.route = {
            "routed_via": ra.get("routed_via", ra.get("source")),
            "alternatives": ra.get("route_trail", []),
            "eligible_for_tools": ra.get("eligible_for_tools"),
            "eligibility_reason": ra.get("eligibility_reason"),
        }
        present["route"] = ("routed_via" in ra or "route_trail" in ra
                            or "source" in ra)
        # 6. final output
        path.final_output = {
            "source": ra.get("source"),
            "handled": ra.get("handled"),
            "used_llm": ra.get("used_llm"),
            "escalated": ra.get("escalated"),
            "chars": ra.get("output_chars"),
        }
        if "output_text" in ra:
            path.final_output["text"] = ra["output_text"]
        present["final_output"] = "source" in ra

    # 4. tool calls — each tool.execute, with its child tool.gate verdict
    for s in spans:
        if s.get("name") != _TOOL_NAME:
            continue
        ta = _attrs(s)
        gate_span = next(
            (g for g in spans
             if g.get("name") == _GATE_NAME
             and g.get("parent_span_id") == s.get("span_id")), None)
        gate = _attrs(gate_span) if gate_span is not None else {}
        gate_rec = {
            "action": gate.get("gate_action"),
            "risk_tier": gate.get("risk_tier"),
            "effective_provenance": gate.get("effective_provenance"),
            "needs_pkexec": gate.get("needs_pkexec"),
        }
        if "gate_reason" in gate:
            gate_rec["reason"] = gate["gate_reason"]
        tool_rec = {
            "tool_name": ta.get("tool_name"),
            "success": ta.get("success"),
            "executed": ta.get("executed"),
            "blocked": ta.get("blocked"),
            "gate": gate_rec,
        }
        if "tool_args" in ta:
            tool_rec["args"] = ta["tool_args"]
        tool_rec["counterfactual"] = _counterfactual(tool_rec, gate_rec)
        path.tool_calls.append(tool_rec)
    present["tool_calls"] = bool(path.tool_calls)

    # 5. synthesis — llm.synth (tool path) or router.llm_freeform (freeform)
    synth = next((s for s in spans if s.get("name") in _SYNTH_NAMES), None)
    if synth is not None:
        sa = _attrs(synth)
        rec: dict[str, Any] = {"via": synth.get("name")}
        # Carry whichever synthesis-input signals the emitting seam recorded.
        for k in ("synthesis_tool", "tool_results_in", "used_model_summary",
                  "input_len", "synthesis_ok", "tokens_prompt",
                  "tokens_completion", "grounding_present", "message_count",
                  "synthesis_query_type", "synthesis_input"):
            if k in sa:
                rec[k] = sa[k]
        path.synthesis = rec
        present["synthesis"] = True

    path._present = present
    return path


def group_by_trace(spans: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Bucket a flat span stream by ``trace_id`` (one bucket == one turn)."""
    out: dict[str, list[dict[str, Any]]] = {}
    for s in spans:
        out.setdefault(s.get("trace_id", ""), []).append(s)
    return out


def read_spans(path: str | Path) -> list[dict[str, Any]]:
    """Read all span records from a ``decisions.jsonl`` file (skips torn lines)."""
    p = Path(path)
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def load_trace(path: str | Path, trace_id: str | None = None) -> DecisionPath:
    """Reconstruct one turn's decision path from a ``decisions.jsonl`` file.

    With ``trace_id`` given, reconstructs that turn; without it, reconstructs the
    single trace in the file (raises if the file holds more than one — the caller
    must disambiguate). This is the load path the harness / a reviewer uses on a
    ``--observe`` run's ``<run_dir>/intergen/decisions.jsonl``.
    """
    grouped = group_by_trace(read_spans(path))
    if trace_id is None:
        ids = [t for t in grouped if t]
        if len(ids) != 1:
            raise ValueError(
                f"{path} holds {len(ids)} traces; pass trace_id to disambiguate")
        trace_id = ids[0]
    return reconstruct(grouped.get(trace_id, []))
