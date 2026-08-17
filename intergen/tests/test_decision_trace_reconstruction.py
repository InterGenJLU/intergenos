# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Phase-1 exit proof — a scenario's FULL decision path reconstructs from the
trace alone.

Two layers:

* **Runtime emission (integration):** drive the REAL router.route() for a tool-
  firing turn ("list the printers", the 06-11 harness plan's Verification #1
  scenario) with a mocked model + a REAL ToolRegistry + a read-only stub tool,
  tracing on. Then reconstruct the six-element decision path from the emitted
  ``decisions.jsonl`` ALONE and assert it is complete. This proves the runtime
  seams (route / llm_tools / tool.execute / tool.gate / llm.synth) actually fire
  and correlate under one trace_id — the exit criterion.

* **Reconstruction contract (unit, deterministic):** feed synthetic-but-faithful
  span sets to :mod:`intergen.tests.trace_reconstruct` to pin the six-element
  assembly, the tool-gate counterfactual read, the freeform path, graceful
  degradation on missing spans, and the file loader — independent of the router
  cascade so the contract is stable even as routing evolves.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import intergen.trace as trace_mod
from intergen.tests import trace_reconstruct as tr
from intergen.interfaces.tool import BaseTool
from intergen.interfaces.types import (
    LLMResponse, Message, MessageRole, RouteResult, SafetyTier, ToolCall,
    ToolResult, ToolSchema,
)
from intergen.interfaces.provenance import (
    ConversationTrustState, IngressTracker, Provenance,
)


# ── synthetic span helpers (trace.Span.as_record() shape) ──

def _span(name, kind, span_id, parent, seq, **attrs):
    return {
        "schema_version": 1, "trace_id": "T1", "span_id": span_id,
        "parent_span_id": parent, "seq": seq, "name": name, "kind": kind,
        "start_ms": 0.0, "duration_ms": 1.0, "status": "ok",
        "status_message": "", "attributes": attrs,
    }


def _full_tool_path_spans():
    """A faithful full six-element trace for a tool-firing turn."""
    return [
        _span("router.route", "request", "r", None, 0,
              input_chars=17, input_text="list the printers",
              query_type="diagnostic", semantic_score=0.42,
              semantic_runner_up=0.31, semantic_gap=0.11,
              semantic_intent_id=None, needs_decomposition=False,
              eligible_for_tools=True,
              eligibility_reason="native_freeform_schema_exposure",
              source="llm_tools", routed_via="llm_tools", handled=True,
              used_llm=True, escalated=False, output_chars=20,
              output_text="You have 2 printers.",
              route_trail=[
                  {"stage": "classify", "outcome": "info",
                   "query_type": "diagnostic"},
                  {"stage": "decompose", "outcome": "info",
                   "needs_decomposition": False},
                  {"stage": "keyword", "outcome": "rejected", "matched": False},
                  {"stage": "semantic", "outcome": "rejected",
                   "score": 0.42, "gap": 0.11},
                  {"stage": "eligibility", "outcome": "info", "eligible": True},
                  {"stage": "llm_tools", "outcome": "won"}]),
        _span("router.llm_tools", "llm", "lt", "r", 1,
              tool_calls=["list_printers"], dispatch_any_failed=False,
              dispatch_any_blocked=False, dispatch_any_denied=False,
              tokens_prompt=30, tokens_completion=8),
        _span("tool.execute", "tool", "te", "lt", 2, tool_name="list_printers",
              tool_args={"source_of_request": "user_direct"},
              success=True, executed=True, blocked=False),
        _span("tool.gate", "gate", "tg", "te", 3, gate_action="execute",
              risk_tier="read_only", effective_provenance="user_direct",
              needs_pkexec=False, gate_reason="read-only, user-direct"),
        _span("llm.synth", "llm", "ls", "lt", 4, synthesis_tool="list_printers",
              tool_results_in=1, used_model_summary=False, input_len=20,
              synthesis_ok=True, tokens_prompt=40, tokens_completion=8),
    ]


class ReconstructionContractTests(unittest.TestCase):
    def test_full_tool_path_is_complete(self):
        path = tr.reconstruct(_full_tool_path_spans())
        self.assertTrue(
            path.is_complete(require_tools=True, require_synthesis=True),
            path.render())
        self.assertEqual(path.missing_elements(), [])
        self.assertEqual(set(path.elements_present().values()), {True})

    def test_all_six_elements_recovered(self):
        p = tr.reconstruct(_full_tool_path_spans())
        # 1 input
        self.assertEqual(p.input["chars"], 17)
        self.assertEqual(p.input["text"], "list the printers")
        # 2 classification + why
        self.assertEqual(p.classification["query_type"], "diagnostic")
        self.assertEqual(p.classification["semantic_gap"], 0.11)
        # 3 route + alternatives considered
        self.assertEqual(p.route["routed_via"], "llm_tools")
        stages = [s["stage"] for s in p.route["alternatives"]]
        self.assertEqual(stages,
                         ["classify", "decompose", "keyword", "semantic",
                          "eligibility", "llm_tools"])
        self.assertEqual(p.route["alternatives"][-1]["outcome"], "won")
        # 4 tool call + gate verdict + counterfactual
        self.assertEqual(len(p.tool_calls), 1)
        self.assertEqual(p.tool_calls[0]["tool_name"], "list_printers")
        self.assertEqual(p.tool_calls[0]["gate"]["action"], "execute")
        self.assertEqual(p.tool_calls[0]["counterfactual"], "fired")
        # 5 synthesis inputs
        self.assertEqual(p.synthesis["via"], "llm.synth")
        self.assertEqual(p.synthesis["synthesis_tool"], "list_printers")
        # 6 final output
        self.assertEqual(p.final_output["source"], "llm_tools")
        self.assertTrue(p.final_output["handled"])

    def test_render_shows_the_path(self):
        text = tr.reconstruct(_full_tool_path_spans()).render()
        for needle in ("1. input", "2. classify", "3. route",
                       "WON='llm_tools'", "4. tools", "list_printers",
                       "5. synthesis", "6. output", "complete=True"):
            self.assertIn(needle, text)

    def test_freeform_path_complete_without_tools(self):
        spans = [
            _span("router.route", "request", "r", None, 0, input_chars=25,
                  query_type="general", semantic_score=0.12,
                  semantic_gap=0.12, semantic_intent_id=None,
                  needs_decomposition=False, source="llm_freeform",
                  routed_via="llm_freeform", handled=True, used_llm=True,
                  escalated=False, output_chars=40,
                  route_trail=[{"stage": "classify", "outcome": "info"},
                               {"stage": "llm_freeform", "outcome": "won"}]),
            _span("router.llm_freeform", "llm", "ff", "r", 1, tokens_prompt=15,
                  tokens_completion=20, grounding_present=False,
                  message_count=2, synthesis_query_type="general"),
        ]
        p = tr.reconstruct(spans)
        self.assertTrue(p.is_complete(require_synthesis=True), p.render())
        self.assertEqual(p.tool_calls, [])
        self.assertEqual(p.synthesis["via"], "router.llm_freeform")
        # tools not required on a freeform turn
        self.assertFalse(p.is_complete(require_tools=True))

    def test_gate_counterfactuals(self):
        # denied by the gate (reject) — proposed but should-not-fire
        denied = [
            _span("router.route", "request", "r", None, 0, input_chars=10,
                  query_type="diagnostic", source="llm_tools",
                  routed_via="llm_tools", handled=True, used_llm=True),
            _span("tool.execute", "tool", "te", "r", 1, tool_name="manage_services",
                  success=False, executed=False, blocked=False),
            _span("tool.gate", "gate", "tg", "te", 2, gate_action="reject",
                  risk_tier="privileged_state_changing"),
        ]
        p = tr.reconstruct(denied)
        self.assertEqual(p.tool_calls[0]["counterfactual"], "blocked_by_gate")

        # hard safety block
        blocked = [
            _span("router.route", "request", "r", None, 0, input_chars=10,
                  query_type="diagnostic", source="llm_tools",
                  routed_via="llm_tools", handled=True),
            _span("tool.execute", "tool", "te", "r", 1, tool_name="run_command",
                  success=False, executed=False, blocked=True),
        ]
        self.assertEqual(
            tr.reconstruct(blocked).tool_calls[0]["counterfactual"],
            "safety_blocked")

        # ran but failed
        failed = [
            _span("router.route", "request", "r", None, 0, input_chars=10,
                  query_type="diagnostic", source="llm_tools", handled=True),
            _span("tool.execute", "tool", "te", "r", 1, tool_name="run_command",
                  success=False, executed=True, blocked=False),
            _span("tool.gate", "gate", "tg", "te", 2, gate_action="execute"),
        ]
        self.assertEqual(
            tr.reconstruct(failed).tool_calls[0]["counterfactual"], "failed")

    def test_missing_spans_degrade_gracefully(self):
        # root only: base elements present, tool_calls + synthesis absent
        root_only = [_full_tool_path_spans()[0]]
        p = tr.reconstruct(root_only)
        self.assertEqual(sorted(p.missing_elements()),
                         ["synthesis", "tool_calls"])
        self.assertTrue(p.is_complete())  # base four present
        self.assertFalse(p.is_complete(require_tools=True))

    def test_empty_trace(self):
        p = tr.reconstruct([])
        self.assertEqual(p.trace_id, "")
        self.assertEqual(len(p.missing_elements()), len(tr.ELEMENTS))


class TraceFileLoaderTests(unittest.TestCase):
    def test_load_trace_and_read_skips_torn_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "decisions.jsonl"
            with open(p, "w") as f:
                for s in _full_tool_path_spans():
                    f.write(json.dumps(s) + "\n")
                f.write("{ this is a torn tail line\n")  # must be skipped
            # single trace in the file → no trace_id needed
            path = tr.load_trace(p)
            self.assertTrue(
                path.is_complete(require_tools=True, require_synthesis=True))
            self.assertEqual(path.trace_id, "T1")

    def test_load_trace_multi_trace_requires_disambiguation(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "decisions.jsonl"
            second = _span("router.route", "request", "r2", None, 9,
                           source="cache", routed_via="cache", handled=True,
                           query_type="general", input_chars=5)
            second["trace_id"] = "T2"
            with open(p, "w") as f:
                for s in _full_tool_path_spans():
                    f.write(json.dumps(s) + "\n")
                f.write(json.dumps(second) + "\n")
            with self.assertRaises(ValueError):
                tr.load_trace(p)                      # ambiguous → must raise
            # naming the trace resolves it
            self.assertEqual(tr.load_trace(p, "T2").route["routed_via"], "cache")


# ── integration: real runtime emission, reconstructed from the trace alone ──

class _ListPrinters(BaseTool):
    """A read-only (AUTO) stub tool — the gate returns 'execute', so the turn
    exercises the real tool.execute + tool.gate span path end to end."""

    @property
    def name(self) -> str:
        return "list_printers"

    @property
    def description(self) -> str:
        return "List the printers configured on this machine."

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="list_printers",
            description="List configured printers.",
            parameters={"type": "object", "properties": {}, "required": []},
            safety_tier=SafetyTier.AUTO,
        )

    def execute(self, arguments):
        return ToolResult(call_id="c0", name="list_printers",
                          content="printer-1\nprinter-2", success=True,
                          executed=True)


def _tool_router():
    """A ConversationRouter wired for a real tool-firing turn: real registry +
    stub tool, mocked model, cascade deps stubbed the way test_router_trace does."""
    from intergen.router import ConversationRouter
    from intergen.tool_registry import ToolRegistry

    r = ConversationRouter.__new__(ConversationRouter)
    r._ingress_tracker = IngressTracker()
    r._trust_state = ConversationTrustState()
    r._review_callback = None
    r._lock_dispatch = False          # NATIVE posture → tool-eligible
    r._metrics = None
    r._state_cache = None
    r._memory = None
    r._first_interaction = False
    r._hardware_tier = None

    reg = ToolRegistry()              # fresh registry defaults to unlocked
    reg.register(_ListPrinters())
    r._tools = reg

    sem = mock.Mock()
    sem._normalize_input.side_effect = lambda x: x
    sem._match_embeddings.return_value = mock.Mock(
        score=0.12, intent_id=None, runner_up_score=0.0)
    r._semantic = sem

    llm = mock.Mock()
    llm.stream_with_tools.return_value = iter([
        ToolCall(name="list_printers", arguments={},
                 source_of_request=Provenance.USER_DIRECT)])
    llm.continue_after_tool_call.return_value = LLMResponse(
        text="You have 2 printers: printer-1, printer-2.", model="local",
        tokens_prompt=30, tokens_completion=9)
    r._llm = llm

    r._grounding_context = lambda *a, **k: ""
    r._build_messages = lambda *a, **k: [
        Message(role=MessageRole.USER, content="list the printers")]
    r._append_history = lambda *a, **k: None
    return r


class RuntimeEmissionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(setattr, trace_mod, "_tracer", None)

    def test_tool_turn_reconstructs_from_trace_alone(self):
        r = _tool_router()
        with mock.patch.dict(os.environ, {
                    "INTERGEN_TRACE": "1", "INTERGEN_TRACE_CONTENT": "1",
                    "XDG_STATE_HOME": self.state}), \
             mock.patch("intergen.router.analyze_query",
                        return_value=mock.Mock(needs_decomposition=False,
                                               is_compound=False,
                                               sub_queries=[])), \
             mock.patch("intergen.router.is_destructive_execution",
                        return_value=False), \
             mock.patch("intergen.router.is_destructive_intent",
                        return_value=False), \
             mock.patch.object(type(r), "_classify_query_type",
                               return_value="diagnostic"), \
             mock.patch.object(type(r), "_try_keyword_match",
                               return_value=RouteResult(handled=False)), \
             mock.patch.object(type(r), "_looks_like_state_question",
                               return_value=False), \
             mock.patch.object(type(r), "_try_file_lifecycle",
                               return_value=None), \
             mock.patch.object(type(r), "_record"):
            trace_mod._tracer = None
            res = r.route("list the printers")

        self.assertEqual(res.source, "llm_tools")
        self.assertNotEqual(res.trace_id, "")

        # Reconstruct the decision path from the emitted trace ALONE.
        decisions = Path(self.state) / "intergen" / "decisions.jsonl"
        path = tr.load_trace(decisions, res.trace_id)

        self.assertTrue(
            path.is_complete(require_tools=True, require_synthesis=True),
            "\n" + path.render())
        self.assertEqual(path.missing_elements(), [])

        # The six elements, from the REAL runtime spans:
        self.assertEqual(path.input["text"], "list the printers")   # 1
        self.assertEqual(path.classification["query_type"], "diagnostic")  # 2
        self.assertEqual(path.route["routed_via"], "llm_tools")     # 3
        stages = [s["stage"] for s in path.route["alternatives"]]
        self.assertIn("classify", stages)
        self.assertIn("eligibility", stages)
        self.assertEqual(stages[-1], "llm_tools")                   # winner last
        self.assertEqual(len(path.tool_calls), 1)                   # 4
        self.assertEqual(path.tool_calls[0]["tool_name"], "list_printers")
        self.assertEqual(path.tool_calls[0]["gate"]["action"], "execute")
        self.assertEqual(path.tool_calls[0]["counterfactual"], "fired")
        self.assertIsNotNone(path.synthesis)                        # 5
        self.assertEqual(path.synthesis["via"], "llm.synth")
        self.assertEqual(path.final_output["source"], "llm_tools")  # 6

    def test_tracing_off_is_transparent(self):
        r = _tool_router()
        with mock.patch.dict(os.environ,
                             {"INTERGEN_TRACE": "", "XDG_STATE_HOME": self.state}), \
             mock.patch("intergen.router.analyze_query",
                        return_value=mock.Mock(needs_decomposition=False,
                                               is_compound=False,
                                               sub_queries=[])), \
             mock.patch("intergen.router.is_destructive_execution",
                        return_value=False), \
             mock.patch("intergen.router.is_destructive_intent",
                        return_value=False), \
             mock.patch.object(type(r), "_classify_query_type",
                               return_value="diagnostic"), \
             mock.patch.object(type(r), "_try_keyword_match",
                               return_value=RouteResult(handled=False)), \
             mock.patch.object(type(r), "_looks_like_state_question",
                               return_value=False), \
             mock.patch.object(type(r), "_try_file_lifecycle",
                               return_value=None), \
             mock.patch.object(type(r), "_record"):
            trace_mod._tracer = None
            res = r.route("list the printers")
        # Same route, no trace written, no trace_id — tracing changed nothing.
        self.assertEqual(res.source, "llm_tools")
        self.assertEqual(res.trace_id, "")
        self.assertFalse((Path(self.state) / "intergen" / "decisions.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
