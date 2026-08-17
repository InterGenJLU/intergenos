# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Dispatch-lockdown tests (the 2B): the model NEVER touches dispatch or args.

Covers the user-side lane of the lockdown:
  - the router P3 gate at the single chokepoint (_try_llm_tools), proven from
    BOTH reachable call sites — route()'s eligibility path and _route_single()'s
    unconditional call (WC red-team #1's two ungated sites);
  - the fail-closed default (a router without an explicit unlock stays locked);
  - the tool-registry structural backstop (get_tool_schemas → [] when locked,
    so the model is never offered tools on any surface — WC guard #2).

Code-owned arg extraction (_extract_arguments for write_file / analyze_file /
take_screenshot) is owned by the matcher-corpus lane and verified there + by its
P/R harness; not duplicated here.

The lightweight ConversationRouter.__new__ pattern (no LLM, no embedder) mirrors
test_router_compound_guard / test_router_explain.
"""
from __future__ import annotations

import unittest
import unittest.mock as mock

from intergen.router import ConversationRouter
from intergen.interfaces.types import RouteResult
from intergen.tool_registry import ToolRegistry


def _bare_router():
    return ConversationRouter.__new__(ConversationRouter)


class P3GateTests(unittest.TestCase):
    def test_try_llm_tools_unhandled_when_locked(self):
        # The chokepoint returns handled=False at the entry — the model is never
        # consulted (get_tool_schemas + stream_with_tools never called).
        r = _bare_router()
        r._lock_dispatch = True
        r._llm = mock.Mock()
        r._tools = mock.Mock()
        with mock.patch.object(r, "_grounding_context"), \
             mock.patch.object(r, "_build_messages"):
            result = r._try_llm_tools("install firefox")
        self.assertFalse(result.handled)
        r._tools.get_tool_schemas.assert_not_called()
        r._llm.stream_with_tools.assert_not_called()

    def test_fail_closed_default_when_lock_attr_missing(self):
        # A partially-constructed router with no _lock_dispatch set is LOCKED by
        # default (getattr(..., True)) — fail-closed, never leaks dispatch.
        r = _bare_router()  # deliberately does NOT set _lock_dispatch
        r._llm = mock.Mock()
        r._tools = mock.Mock()
        result = r._try_llm_tools("install firefox")
        self.assertFalse(result.handled)
        r._tools.get_tool_schemas.assert_not_called()

    def test_unlocked_reaches_the_model_offer_path(self):
        # Unlocked: the entry gate does NOT short-circuit; the model-offer path is
        # entered (get_tool_schemas consulted). Empty schemas → early return, but
        # the call proves the gate let it through.
        r = _bare_router()
        r._lock_dispatch = False
        r._tools = mock.Mock()
        r._tools.get_tool_schemas.return_value = []
        r._llm = mock.Mock()
        with mock.patch.object(r, "_grounding_context", return_value=None), \
             mock.patch.object(r, "_build_messages", return_value=[]):
            r._try_llm_tools("install firefox")
        r._tools.get_tool_schemas.assert_called_once()

    def test_route_single_locked_skips_llm_tools_to_freeform(self):
        # The _route_single seam (WC #1's easy-to-miss unconditional call site):
        # under lockdown the REAL _try_llm_tools returns unhandled, so the sub
        # falls through to freeform — the model never gets the dispatch decision.
        r = _bare_router()
        r._lock_dispatch = True
        r._llm = mock.Mock()
        r._tools = mock.Mock()
        unhandled = RouteResult(handled=False)
        freeform = RouteResult(text="free", source="llm_freeform", handled=True)
        with mock.patch.object(r, "_try_keyword_match", return_value=unhandled), \
             mock.patch.object(r, "_try_semantic_match", return_value=unhandled), \
             mock.patch.object(r, "_looks_like_state_question", return_value=False), \
             mock.patch.object(r, "_grounding_context"), \
             mock.patch.object(r, "_build_messages"), \
             mock.patch.object(r, "_try_llm_freeform", return_value=freeform):
            result = r._route_single("a decomposed sub query")
        self.assertIs(result, freeform)
        r._tools.get_tool_schemas.assert_not_called()
        r._llm.stream_with_tools.assert_not_called()


class RegistryBackstopTests(unittest.TestCase):
    def test_locked_registry_returns_no_schemas(self):
        reg = ToolRegistry()
        reg.discover_tools()
        self.assertGreater(len(reg.get_tool_schemas()), 0)  # unlocked: tools offered
        reg.set_tool_offering_locked(True)
        self.assertEqual(reg.get_tool_schemas(), [])        # locked: none offered
        reg.set_tool_offering_locked(False)
        self.assertGreater(len(reg.get_tool_schemas()), 0)  # restored


if __name__ == "__main__":
    unittest.main()
