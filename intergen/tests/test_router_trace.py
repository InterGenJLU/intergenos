# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Router ↔ decision-trace integration (the route() traced wrapper).

route() wraps _route_impl in a root "router.route" span, stamps the result's
trace_id from the active trace, and records the routing decision. These tests
pin that integration via the empty-input fast path (which only touches
self._ingress_tracker before returning), so no model/embeddings are needed:

* tracing ON  → result.trace_id is set, one "router.route" span is written, and
  it records the final source.
* tracing OFF → result.trace_id stays "" and nothing is written (transparent).
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import intergen.trace as trace_mod
from intergen.router import ConversationRouter


def _records(state_dir: str) -> list[dict]:
    p = Path(state_dir) / "intergen" / "decisions.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line]


def _minimal_router() -> ConversationRouter:
    # Empty-input path only calls self._ingress_tracker.reset() and
    # self._classify_query_type() (class-method) before returning, so a bare
    # instance plus a mock ingress tracker is enough — no model/embeddings.
    r = ConversationRouter.__new__(ConversationRouter)
    r._ingress_tracker = mock.Mock()
    # The P3-span trace test needs the native tool path reachable; the dispatch-
    # lockdown default is fail-closed locked (see test_dispatch_lockdown). Other
    # trace tests here use non-eligible inputs, so unlocking is inert for them.
    r._lock_dispatch = False
    return r


class RouterTraceIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.state = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        # Each test controls the tracer singleton; always reset it afterward so
        # an enabled tracer never leaks into the rest of the suite.
        self.addCleanup(setattr, trace_mod, "_tracer", None)

    def test_route_populates_trace_id_and_emits_root_span_when_enabled(self) -> None:
        r = _minimal_router()
        with mock.patch.dict(os.environ,
                             {"INTERGEN_TRACE": "1", "XDG_STATE_HOME": self.state}):
            trace_mod._tracer = None  # force reconstruct as enabled under this env
            res = r.route("")

        self.assertEqual(res.source, "empty_input")
        self.assertTrue(res.handled)
        self.assertNotEqual(res.trace_id, "")

        spans = [s for s in _records(self.state) if s["name"] == "router.route"]
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span["kind"], "request")
        self.assertIsNone(span["parent_span_id"])           # root
        self.assertEqual(span["trace_id"], res.trace_id)    # result joins its trace
        self.assertEqual(span["attributes"]["source"], "empty_input")
        self.assertTrue(span["attributes"]["handled"])
        self.assertFalse(span["attributes"]["used_llm"])
        self.assertIn("query_type", span["attributes"])
        self.assertIsNotNone(span["duration_ms"])

    def test_route_trace_id_empty_and_no_write_when_disabled(self) -> None:
        r = _minimal_router()
        env = {"XDG_STATE_HOME": self.state, "INTERGEN_TRACE": ""}
        with mock.patch.dict(os.environ, env):
            trace_mod._tracer = None  # reconstruct as disabled
            res = r.route("")

        self.assertEqual(res.source, "empty_input")
        self.assertEqual(res.trace_id, "")
        self.assertEqual(_records(self.state), [])

    def test_route_records_decision_inputs_on_a_full_cascade_path(self) -> None:
        # A non-special query, run decide_only so the LLM seams return a route
        # decision WITHOUT generating — exercises every annotation point
        # (safety / decompose / semantic / eligibility) with the heavy deps faked.
        from intergen.interfaces.types import RouteResult

        r = _minimal_router()
        r._metrics = None
        r._state_cache = None
        r._memory = None
        r._first_interaction = False
        r._hardware_tier = None  # analyze_query is patched; value unused
        sem = mock.Mock()
        sem._normalize_input.side_effect = lambda x: x
        sem._match_embeddings.return_value = mock.Mock(
        score=0.12, intent_id=None, runner_up_score=0.0)
        r._semantic = sem

        with mock.patch.dict(os.environ,
                             {"INTERGEN_TRACE": "1", "XDG_STATE_HOME": self.state}), \
             mock.patch("intergen.router.analyze_query",
                        return_value=mock.Mock(needs_decomposition=False)), \
             mock.patch.object(type(r), "_try_keyword_match",
                               return_value=RouteResult(handled=False)):
            trace_mod._tracer = None
            res = r.route("what is the meaning of life", decide_only=True)

        # falls through past the fast paths to an LLM route decision
        self.assertIn(res.source, ("llm_tools", "llm_freeform"))
        self.assertNotEqual(res.trace_id, "")

        attrs = [s for s in _records(self.state)
                 if s["name"] == "router.route"][0]["attributes"]
        # decision inputs recorded where computed
        self.assertFalse(attrs["has_safety_trigger"])
        self.assertFalse(attrs["needs_decomposition"])
        self.assertEqual(attrs["semantic_score"], 0.12)
        self.assertIsNone(attrs["semantic_intent_id"])
        self.assertIn("eligible_for_tools", attrs)
        # M8-1 eligibility redesign: under the NATIVE (unlocked) posture — which
        # _minimal_router sets (r._lock_dispatch = False) — a freeform turn is
        # tool-eligible regardless of the old (score>=0.7 or diagnostic/safety)
        # triple. This turn scores 0.12 / query_type "general", which the OLD
        # gate would have STARVED; the redesign offers schemas (the review gate
        # in execute(), not starvation, is the trust boundary).
        self.assertFalse(attrs["dispatch_locked"])
        self.assertTrue(attrs["eligible_for_tools"])
        self.assertEqual(attrs["eligibility_reason"],
                         "native_freeform_schema_exposure")
        # The score signal is retained as an observability annotation, no longer
        # the gate.
        self.assertEqual(attrs["eligibility_inputs"]["semantic_score"], 0.12)

    def _fallthrough_router(self):
        r = _minimal_router()
        r._metrics = None
        r._state_cache = None
        r._memory = None
        r._first_interaction = False
        r._hardware_tier = None
        sem = mock.Mock()
        sem._normalize_input.side_effect = lambda x: x
        sem._match_embeddings.return_value = mock.Mock(
        score=0.12, intent_id=None, runner_up_score=0.0)
        r._semantic = sem
        return r

    def test_p4_llm_freeform_emits_child_span_under_root(self) -> None:
        from intergen.interfaces.types import RouteResult
        r = self._fallthrough_router()
        fake = RouteResult(text="42", source="llm_freeform", handled=True,
                           used_llm=True, tokens_prompt=10, tokens_completion=5)
        with mock.patch.dict(os.environ,
                             {"INTERGEN_TRACE": "1", "XDG_STATE_HOME": self.state}), \
             mock.patch("intergen.router.analyze_query",
                        return_value=mock.Mock(needs_decomposition=False)), \
             mock.patch.object(type(r), "_classify_query_type", return_value="general"), \
             mock.patch.object(type(r), "_try_keyword_match",
                               return_value=RouteResult(handled=False)), \
             mock.patch.object(type(r), "_try_deterministic_fallback",
                               return_value=RouteResult(handled=False)), \
             mock.patch.object(type(r), "_try_llm_tools",
                               return_value=RouteResult(handled=False)), \
             mock.patch.object(type(r), "_try_llm_freeform", return_value=fake), \
             mock.patch.object(type(r), "_record"):
            trace_mod._tracer = None
            # M8-1 redesign: under NATIVE this conversational turn is now tool-
            # eligible, so P3 runs first; the model calls no tool (handled=False)
            # and the turn falls through to P4 freeform — the path this span test
            # pins.
            res = r.route("what is the meaning of life")  # not decide_only

        self.assertEqual(res.source, "llm_freeform")
        recs = _records(self.state)
        root = [s for s in recs if s["name"] == "router.route"][0]
        child = [s for s in recs if s["name"] == "router.llm_freeform"][0]
        self.assertEqual(child["kind"], "llm")
        self.assertEqual(child["parent_span_id"], root["span_id"])
        self.assertEqual(child["trace_id"], root["trace_id"])
        self.assertEqual(child["attributes"]["prompt_tok_count"], 10)
        self.assertEqual(child["attributes"]["completion_tok_count"], 5)
        self.assertIsNotNone(child["duration_ms"])

    def test_p3_llm_tools_emits_child_span_under_root(self) -> None:
        from intergen.interfaces.types import RouteResult
        r = self._fallthrough_router()
        fake = RouteResult(text="done", source="llm_tools", handled=True,
                           used_llm=True, tool_calls=[],
                           tokens_prompt=20, tokens_completion=8)
        with mock.patch.dict(os.environ,
                             {"INTERGEN_TRACE": "1", "XDG_STATE_HOME": self.state}), \
             mock.patch("intergen.router.analyze_query",
                        return_value=mock.Mock(needs_decomposition=False)), \
             mock.patch.object(type(r), "_classify_query_type", return_value="diagnostic"), \
             mock.patch.object(type(r), "_try_keyword_match",
                               return_value=RouteResult(handled=False)), \
             mock.patch.object(type(r), "_try_llm_tools", return_value=fake), \
             mock.patch.object(type(r), "_record"):
            trace_mod._tracer = None
            res = r.route("list the printers")  # diagnostic -> tool-eligible

        self.assertEqual(res.source, "llm_tools")
        recs = _records(self.state)
        root = [s for s in recs if s["name"] == "router.route"][0]
        child = [s for s in recs if s["name"] == "router.llm_tools"][0]
        self.assertEqual(child["kind"], "llm")
        self.assertEqual(child["parent_span_id"], root["span_id"])
        self.assertEqual(child["attributes"]["prompt_tok_count"], 20)
        self.assertEqual(child["attributes"]["tool_calls"], [])
        self.assertTrue(root["attributes"]["eligible_for_tools"])


if __name__ == "__main__":
    unittest.main()
