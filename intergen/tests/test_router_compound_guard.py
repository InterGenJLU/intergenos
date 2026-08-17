# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Parity-lock for the _route_single route-to-tools guard (PI-218-3).

A decomposed sub-query that is a system-state question must get the same fast
deterministic dispatch (_try_deterministic_fallback) the top-level route() path
runs BEFORE the slow LLM tool-selection. Without the guard every such sub-query
fell to _try_llm_tools — a ~50s call per sub on the Tier-2 iGPU floor (measured
on a development machine: an all-state 2-sub compound dropped ~100s -> ~40ms daemon-side, llm
False, once the guard dispatched both subs deterministically). This test pins
the ordering so a future refactor cannot silently drop a sub back into the
llm_tools path; it also pins the gate (a non-state sub is NOT hijacked into a
command dispatch and falls through to llm_tools unchanged).

Exercises ConversationRouter._route_single in isolation (ConversationRouter.__new__,
the lightweight pattern used by test_router_offer / test_router_explain) — no heavy
construction, no LLM, no embedding server.
"""
from __future__ import annotations

import unittest
import unittest.mock as mock

from intergen.router import ConversationRouter
from intergen.interfaces.types import RouteResult


def _bare_router():
    return ConversationRouter.__new__(ConversationRouter)


class RouteSingleGuardParityTests(unittest.TestCase):
    def test_state_question_sub_uses_deterministic_guard_not_llm_tools(self):
        # P1 keyword + P2 semantic miss on phrasing; the sub is a state question,
        # so the guard must dispatch it deterministically and llm_tools/llm_freeform
        # must never be reached.
        r = _bare_router()
        unhandled = RouteResult(handled=False)
        guard_hit = RouteResult(
            text="RAM: 16 GB total, 4.5 GB used.",
            source="keyword", handled=True, used_llm=False,
        )
        with mock.patch.object(r, "_try_keyword_match", return_value=unhandled), \
             mock.patch.object(r, "_try_semantic_match", return_value=unhandled), \
             mock.patch.object(r, "_looks_like_state_question", return_value=True), \
             mock.patch.object(r, "_try_deterministic_fallback",
                               return_value=guard_hit) as guard, \
             mock.patch.object(r, "_try_llm_tools") as llm_tools, \
             mock.patch.object(r, "_try_llm_freeform") as llm_freeform:
            result = r._route_single("how much memory do I have")

        self.assertIs(result, guard_hit)
        self.assertFalse(result.used_llm)
        guard.assert_called_once()
        llm_tools.assert_not_called()
        llm_freeform.assert_not_called()

    def test_non_state_question_sub_skips_guard_falls_to_llm_tools(self):
        # A non-state sub must NOT hit the guard (gated by _looks_like_state_question)
        # — it falls through to llm_tools unchanged, so no regression for those.
        r = _bare_router()
        unhandled = RouteResult(handled=False)
        tools_hit = RouteResult(
            text="...", source="llm_tools", handled=True, used_llm=True,
        )
        with mock.patch.object(r, "_try_keyword_match", return_value=unhandled), \
             mock.patch.object(r, "_try_semantic_match", return_value=unhandled), \
             mock.patch.object(r, "_looks_like_state_question", return_value=False), \
             mock.patch.object(r, "_try_deterministic_fallback") as guard, \
             mock.patch.object(r, "_try_llm_tools", return_value=tools_hit) as llm_tools:
            result = r._route_single("write me a poem about the sea")

        self.assertIs(result, tools_hit)
        guard.assert_not_called()
        llm_tools.assert_called_once()

    def test_guard_resolving_nothing_falls_through_to_llm_tools(self):
        # State question, but the deterministic selector resolves nothing
        # (handled=False) — must fall through to llm_tools, never silently drop.
        r = _bare_router()
        unhandled = RouteResult(handled=False)
        tools_hit = RouteResult(
            text="...", source="llm_tools", handled=True, used_llm=True,
        )
        with mock.patch.object(r, "_try_keyword_match", return_value=unhandled), \
             mock.patch.object(r, "_try_semantic_match", return_value=unhandled), \
             mock.patch.object(r, "_looks_like_state_question", return_value=True), \
             mock.patch.object(r, "_try_deterministic_fallback",
                               return_value=unhandled) as guard, \
             mock.patch.object(r, "_try_llm_tools", return_value=tools_hit) as llm_tools:
            result = r._route_single("what is the airspeed of an unladen swallow")

        self.assertIs(result, tools_hit)
        guard.assert_called_once()
        llm_tools.assert_called_once()


if __name__ == "__main__":
    unittest.main()
