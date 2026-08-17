# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The intent-dispatch fast path carries a call id, so linkage joins on identity.

The answer→dispatch linkage check compares the linkage's `call_id` with the
result's when BOTH carry one, and falls back to comparing tool NAMES when either
is missing. `_execute_tool_for_intent` — the helper behind the keyword and
semantic fast paths, which is where most deterministic dispatches happen —
constructed its ToolCall without an id, so every one of those turns joined on the
tool name alone.

A name is not an identity. Two calls of the same tool in one turn are
indistinguishable under a name join, so an answer composed from one `run_command`
result while a DIFFERENT `run_command` result sat in hand compared equal and read
as correctly linked. The substitution class the linkage signal exists to catch
was therefore invisible on exactly the path that dispatches most often, and the
id-comparison leg of `_is_substituted` was never reached from it.

These fixtures pin both halves: the helper stamps a non-empty, per-call id onto
the ToolCall it dispatches, and the id-comparison leg distinguishes two
same-named dispatches that a name join cannot.
"""

from __future__ import annotations

import unittest
from unittest import mock

from intergen.interfaces.types import AnswerLinkage, ToolCall, ToolResult
from intergen.router import ConversationRouter
from intergen.safety import _is_substituted


class _FakeTools:
    """Minimal tool registry: `execute` echoes the call back so a fixture can
    read the id the router actually stamped onto the dispatched ToolCall."""

    def __init__(self) -> None:
        self.executed: list[ToolCall] = []

    def get_tool(self, name):
        return object()

    def execute(self, call, **_kwargs):
        self.executed.append(call)
        return ToolResult(call_id=call.call_id, name=call.name, content="ok")


class FastPathStampsACallId(unittest.TestCase):

    def _router(self) -> ConversationRouter:
        r = ConversationRouter.__new__(ConversationRouter)
        r._tools = _FakeTools()
        r._ingress_tracker = None
        r._trust_state = None
        r._review_callback = None
        r._extract_arguments = mock.Mock(return_value={"command": "df -h"})
        return r

    def test_the_dispatched_call_carries_a_non_empty_id(self) -> None:
        r = self._router()
        call, result = r._execute_tool_for_intent("run_command", "disk usage")

        self.assertIsNotNone(call)
        self.assertTrue(call.call_id,
                        "the fast-path dispatch left call_id empty — linkage "
                        "can only join on the tool name")
        self.assertEqual(result.call_id, call.call_id,
                         "the result did not carry the id the call was stamped with")

    def test_each_dispatch_gets_its_own_id(self) -> None:
        r = self._router()
        first, _ = r._execute_tool_for_intent("run_command", "disk usage")
        second, _ = r._execute_tool_for_intent("run_command", "disk usage")

        self.assertNotEqual(first.call_id, second.call_id,
                            "two dispatches of the same tool share an id — the "
                            "join is no better than the tool name")


class IdComparisonDistinguishesSameNamedDispatches(unittest.TestCase):
    """The leg the stamped id makes reachable from the fast path."""

    def test_a_different_call_of_the_same_tool_reads_as_substituted(self) -> None:
        delivered_from = AnswerLinkage(kind="dispatch", tool="run_command",
                                       call_id="aaaa1111", renderer="template")
        other_result = ToolResult(call_id="bbbb2222", name="run_command",
                                  content="Filesystem  Size  Used")

        self.assertTrue(
            _is_substituted(other_result, delivered_from),
            "two same-named dispatches compared equal — the answer was composed "
            "from one result while another was in hand")

    def test_the_same_call_reads_as_correctly_linked(self) -> None:
        linkage = AnswerLinkage(kind="dispatch", tool="run_command",
                                call_id="aaaa1111", renderer="template")
        result = ToolResult(call_id="aaaa1111", name="run_command",
                            content="Filesystem  Size  Used")

        self.assertFalse(_is_substituted(result, linkage))

    def test_an_unstamped_result_still_falls_back_to_the_tool_name(self) -> None:
        """The fallback stays intact: a route that records the tool but not the
        id must keep matching on the tool, never raise a false substitution."""
        linkage = AnswerLinkage(kind="dispatch", tool="run_command",
                                call_id="aaaa1111", renderer="template")
        result = ToolResult(call_id="", name="run_command", content="ok")

        self.assertFalse(_is_substituted(result, linkage))


if __name__ == "__main__":
    unittest.main()
