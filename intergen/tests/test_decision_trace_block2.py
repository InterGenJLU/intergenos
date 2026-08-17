# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Phase-1 trace follow-ons (CUT-032 BLOCK 2) — the trail stays reconstruction-whole.

Two rides, both proven to keep the decision path reconstructable from
``decisions.jsonl`` alone:

  (a) fast-path guard trail notes — a deterministic guard (cache / identity /
      memory / ip / explain / current-data offer) that short-circuits routing now
      records WHY it fired, as a single "won" trail entry (no duplicate winner).
  (b) _route_single sub-query trail — a decomposed sub-query's own P1→P4 cascade
      is recorded on the same per-turn trail, tagged with its scope + text, so a
      compound turn reconstructs whole instead of leaving an opaque gap.

These assert against :mod:`intergen.tests.trace_reconstruct` (the contract the
11-test exit proof pins) plus the router's trail-emit behavior directly.
"""

from __future__ import annotations

import unittest
from unittest import mock

from intergen.tests import trace_reconstruct as tr
from intergen.interfaces.types import RouteResult


def _root_with_trail(trail, **extra):
    """A minimal router.route root span carrying a given route_trail."""
    attrs = {
        "input_chars": 12, "query_type": "general", "semantic_score": 0.1,
        "semantic_gap": 0.1, "semantic_intent_id": None,
        "needs_decomposition": False, "source": extra.pop("source", "cache"),
        "routed_via": extra.pop("routed_via", "cache"), "handled": True,
        "used_llm": False, "escalated": False, "output_chars": 20,
        "route_trail": trail,
    }
    attrs.update(extra)
    return {
        "schema_version": 1, "trace_id": "B2", "span_id": "r",
        "parent_span_id": None, "seq": 0, "name": "router.route",
        "kind": "request", "start_ms": 0.0, "duration_ms": 1.0,
        "status": "ok", "status_message": "", "attributes": attrs,
    }


class FastPathGuardTrailTests(unittest.TestCase):
    def test_guard_why_note_reconstructs_as_winner(self):
        # A cache fast-path: classify info + the guard's own won-note with WHY.
        trail = [
            {"stage": "classify", "outcome": "info", "query_type": "general"},
            {"stage": "cache", "outcome": "won", "single_value": True},
        ]
        path = tr.reconstruct([_root_with_trail(trail)])
        self.assertTrue(path.is_complete())               # base four present
        alts = path.route["alternatives"]
        self.assertEqual(alts[-1]["stage"], "cache")
        self.assertEqual(alts[-1]["outcome"], "won")
        self.assertTrue(alts[-1]["single_value"])         # the WHY survived
        self.assertEqual(path.route["routed_via"], "cache")
        # exactly ONE winner entry (idempotent — no duplicate cache/won)
        self.assertEqual(sum(1 for s in alts if s["outcome"] == "won"), 1)

    def test_render_shows_guard_reason(self):
        trail = [
            {"stage": "classify", "outcome": "info"},
            {"stage": "explain", "outcome": "won", "prior": True, "cited": True},
        ]
        text = tr.reconstruct(
            [_root_with_trail(trail, source="explain", routed_via="explain")]
        ).render()
        self.assertIn("WON='explain'", text)
        self.assertIn("explain", text)


class SubQueryTrailReconstructionTests(unittest.TestCase):
    def test_compound_trail_reconstructs_whole(self):
        # A decomposed turn: top-level decompose frame + two sub-queries' cascades.
        trail = [
            {"stage": "classify", "outcome": "info"},
            {"stage": "decompose", "outcome": "info",
             "needs_decomposition": True},
            {"stage": "keyword", "outcome": "won",
             "scope": "sub_query:1", "sub_query": "how much disk do I have"},
            {"stage": "semantic", "outcome": "rejected",
             "scope": "sub_query:2", "sub_query": "who wrote Hamlet"},
            {"stage": "llm_tools", "outcome": "rejected",
             "scope": "sub_query:2", "sub_query": "who wrote Hamlet"},
            {"stage": "llm_freeform", "outcome": "won",
             "scope": "sub_query:2", "sub_query": "who wrote Hamlet"},
            {"stage": "decomposed", "outcome": "won", "sub_queries": 2},
        ]
        path = tr.reconstruct(
            [_root_with_trail(trail, source="decomposed",
                              routed_via="decomposed")])
        self.assertTrue(path.is_complete())
        alts = path.route["alternatives"]
        # both sub-queries are attributable on the flat trail
        scopes = {s.get("scope") for s in alts if s.get("scope")}
        self.assertEqual(scopes, {"sub_query:1", "sub_query:2"})
        # sub_query:1 resolved at keyword; sub_query:2 fell to freeform
        s1 = [s for s in alts if s.get("scope") == "sub_query:1"]
        self.assertEqual(s1[-1]["stage"], "keyword")
        self.assertEqual(s1[-1]["outcome"], "won")
        s2_last = [s for s in alts if s.get("scope") == "sub_query:2"][-1]
        self.assertEqual((s2_last["stage"], s2_last["outcome"]),
                         ("llm_freeform", "won"))
        self.assertEqual(path.route["routed_via"], "decomposed")


class _StubRouter:
    """A ConversationRouter shell exercising ONLY _route_single's trail emission,
    built via __new__ so no daemon/model deps are constructed."""

    @staticmethod
    def make(*, keyword=False, semantic=False, state=False, llm_tools=False):
        from intergen.router import ConversationRouter
        r = ConversationRouter.__new__(ConversationRouter)
        r._route_trail = []
        r._try_keyword_match = lambda q: RouteResult(handled=keyword, source="keyword")
        r._try_semantic_match = lambda q: RouteResult(handled=semantic, source="semantic")
        r._looks_like_state_question = lambda q: state
        r._try_deterministic_fallback = lambda q: RouteResult(handled=state, source="state")
        r._try_llm_tools = lambda q: RouteResult(handled=llm_tools, source="llm_tools")
        r._try_llm_freeform = lambda q: RouteResult(handled=True, source="llm_freeform")
        return r


class RouteSingleTrailEmitTests(unittest.TestCase):
    def test_scoped_cascade_emits_tagged_notes(self):
        r = _StubRouter.make()   # everything falls through to freeform
        r._route_single("who wrote Hamlet", trail_scope="sub_query:2")
        trail = r._route_trail
        self.assertTrue(trail)
        for step in trail:
            self.assertEqual(step["scope"], "sub_query:2")
            self.assertEqual(step["sub_query"], "who wrote Hamlet")
        self.assertEqual(trail[-1]["stage"], "llm_freeform")
        self.assertEqual(trail[-1]["outcome"], "won")
        stages = [s["stage"] for s in trail]
        self.assertEqual(stages,
                         ["keyword", "semantic", "llm_tools", "llm_freeform"])

    def test_keyword_sub_query_short_circuits_trail(self):
        r = _StubRouter.make(keyword=True)
        r._route_single("how much disk do I have", trail_scope="sub_query:1")
        self.assertEqual([s["stage"] for s in r._route_trail], ["keyword"])
        self.assertEqual(r._route_trail[0]["outcome"], "won")

    def test_no_scope_records_nothing(self):
        # the memory-complaint re-route + direct calls stay silent (unchanged)
        r = _StubRouter.make()
        r._route_single("who wrote Hamlet")
        self.assertEqual(r._route_trail, [])


class WonHelperTests(unittest.TestCase):
    def test_won_appends_single_winner_with_why(self):
        from intergen.router import ConversationRouter
        r = ConversationRouter.__new__(ConversationRouter)
        r._route_trail = []
        r._won("cache", single_value=True)
        self.assertEqual(
            r._route_trail,
            [{"stage": "cache", "outcome": "won", "single_value": True}])


if __name__ == "__main__":
    unittest.main()
