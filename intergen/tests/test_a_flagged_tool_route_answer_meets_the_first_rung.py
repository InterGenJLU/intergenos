# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A flagged answer on the router's tool route meets the reaction ladder's first
rung, as a flagged freeform answer does.

The tool path's answer is screened (stream_with_tools keeps the flags for its
caller), and the router's tool route read none of them: a corrupted text answer
on a tool-enabled turn was flagged, recorded and counted, and then handed to the
person unchanged, where the same corruption on the freeform route is discarded
and the incoherence fallback served in its place (D6, rung 1).

TWO SITES IN THE TOOL ROUTE, one rule.

1. A text answer with no tool call: a flagged one is discarded and the
   incoherence fallback served, through the same _semantic_flag_fallback the
   freeform route uses (its history handling, its scope steer on a general turn,
   its glass decision row), the result naming the tool route as its source.

2. Text the model handed over before a tool call, served when the synthesis of
   the tool's result fails: a flagged one is treated as no text at all, so the
   tool's own result is served instead, as it is when there was no such text.

The flags the route reads are the ones the client kept for THIS call. A call
that handed over no model text screens nothing and would leave an earlier
answer's flags in place; the route clears them first, so a code-owned line is
never discarded on another answer's verdict.

Cells marked CONTROL hold before the change and after it; every other cell
fails before it.
"""

from __future__ import annotations

import unittest
from unittest import mock

from intergen import glass
from intergen.interfaces.types import (
    LLMResponse,
    Message,
    MessageRole,
    ToolResult,
)
from intergen.router import _SEMANTIC_INCOHERENCE_FALLBACK
from intergen.tests import (
    test_a_tool_turn_answer_is_screened_and_a_failed_stream_is_recorded as screened,
)

CORRUPT = screened.CORRUPT
CLEAN = screened.CLEAN
QUESTION = "is sshd enabled?"
TOOL_WORDS = "the tool's own words: sshd is enabled"
REFUSED_LINE = "came back malformed"


class _Registry:
    """The tool schemas a tool-enabled turn is offered, and a dispatch that runs
    nothing on this machine: it records the call and answers a fixed result."""

    def __init__(self):
        self.calls = []

    def get_tool_schemas(self):
        return list(screened._TOOLS)

    def execute(self, call, **_kw):
        self.calls.append(call)
        return ToolResult(call_id=call.call_id, name=call.name,
                          content="enabled", success=True, executed=True)


class _ToolRoute(screened._Case):

    def setUp(self):
        super().setUp()
        self.registry = _Registry()
        self.router = screened._tool_router(self.llm, self.registry)

    def ask(self, *responses):
        with self.answering(*responses), glass.turn("turn-r", "dbus"):
            return self.router._try_llm_tools(QUESTION)

    def decisions(self):
        return [k.get("detail") or {} for a, k in self.rows
                if a[:2] == ("decision", "semantic_flags")]

    def history_texts(self):
        return [m.content for m in self.router._conv.history]


class AFlaggedTextAnswerMeetsTheFirstRung(_ToolRoute):

    def test_the_flagged_answer_is_discarded_and_the_fallback_served(self):
        res = self.ask(screened._Response(screened._text(
            "The sshd service is ", "enabled� and running.")))
        self.assertTrue(res.handled)
        self.assertEqual(res.text, _SEMANTIC_INCOHERENCE_FALLBACK,
                         "a flagged tool-route answer reached the person unchanged")
        self.assertEqual(res.source, "llm_tools")
        self.assertEqual((res.answer_linkage.kind, res.answer_linkage.renderer),
                         ("code", "incoherence_fallback"))
        self.assertEqual(res.confidence, 0.0)
        self.assertEqual(self.decisions(),
                         [{"flags": ["charset_sanity"], "source": "llm_tools",
                           "action": "incoherence_fallback"}])

    def test_the_conversation_keeps_the_fallback_not_the_corrupted_text(self):
        self.ask(screened._Response(screened._text(
            "The sshd service is ", "enabled� and running.")))
        texts = self.history_texts()
        self.assertIn(_SEMANTIC_INCOHERENCE_FALLBACK, texts)
        self.assertNotIn(CORRUPT, texts)

    def test_a_general_turn_carries_the_scope_steer_as_on_the_freeform_route(self):
        from intergen.router import honest_scope_steer
        self.router._current_query_type = "general"
        res = self.ask(screened._Response(screened._text(
            "The sshd service is ", "enabled� and running.")))
        self.assertEqual(res.text, _SEMANTIC_INCOHERENCE_FALLBACK + "\n\n"
                         + honest_scope_steer())

    def test_the_screen_and_its_count_are_unchanged(self):
        """CONTROL: the answer is screened and counted once, as before; only what
        the route does with the flag changes."""
        self.ask(screened._Response(screened._text(
            "The sshd service is ", "enabled� and running.")))
        self.assertEqual([r["raw"] for r in self.model_rows("semantic_health")],
                         [CORRUPT])
        self.assertEqual(self.sink.calls, [["charset_sanity"]])

    def test_a_clean_answer_is_served_as_the_model_gave_it(self):
        """CONTROL"""
        res = self.ask(screened._Response(screened._text(
            "The sshd service is ", "enabled and running.")))
        self.assertEqual(res.text, CLEAN)
        self.assertEqual((res.answer_linkage.kind, res.answer_linkage.renderer),
                         ("model", "llm_tools_text"))
        self.assertEqual(self.decisions(), [])

    def test_a_refused_dialect_line_is_not_discarded_on_an_earlier_answers_flags(self):
        """CONTROL: the line is the code's own, so the call screens nothing; the
        flags an earlier answer left on the client are not this call's."""
        self.llm._last_semantic_flags = ["charset_sanity"]
        res = self.ask(screened._Response(screened._text(
            "<function=format_disk><parameter=device>/dev/sda</parameter>")))
        self.assertIn(REFUSED_LINE, res.text)
        self.assertEqual(self.decisions(), [])


class AFlaggedTextBeforeAToolCallIsNotServed(_ToolRoute):

    def setUp(self):
        super().setUp()
        # The synthesis of the tool's result fails, and the tool's own words are
        # what the route serves then: both stand in for components this rule
        # does not change, each named.
        self.llm.continue_after_tool_call = lambda *a, **k: None
        self.router._synthesize_tool_result = lambda *a, **k: TOOL_WORDS

    def test_a_flagged_text_before_the_tool_call_gives_way_to_the_tools_result(self):
        res = self.ask(screened._Response(
            screened._text("Let me check� that. ", finish=None)
            + screened._tool_call()))
        self.assertEqual(len(self.registry.calls), 1)
        self.assertEqual(res.text, TOOL_WORDS,
                         "the flagged text handed over before the tool call was served")
        self.assertEqual(self.decisions(),
                         [{"flags": ["charset_sanity"], "source": "llm_tools",
                           "action": "tool_result_fallback"}])

    def test_a_clean_text_before_the_tool_call_is_served_as_before(self):
        """CONTROL"""
        res = self.ask(screened._Response(
            screened._text("Let me check that. ", finish=None)
            + screened._tool_call()))
        self.assertEqual(len(self.registry.calls), 1)
        self.assertEqual(res.text, "Let me check that.")
        self.assertEqual(self.decisions(), [])


class TheFreeformRungIsUnchanged(_ToolRoute):

    def test_a_flagged_freeform_answer_meets_the_same_rung(self):
        """CONTROL"""
        response = LLMResponse(text=CORRUPT, model="local",
                               semantic_flags=["charset_sanity"])
        with mock.patch.object(self.llm, "chat", return_value=response):
            with mock.patch.object(self.router, "_grounding_context",
                                   return_value=None), \
                    mock.patch.object(self.router, "_wiki_grounding",
                                      return_value=(None, None, None)), \
                    mock.patch.object(self.router, "_build_messages", return_value=[
                        Message(role=MessageRole.USER, content=QUESTION)]):
                self.router._current_query_type = "diagnostic"
                res = self.router._try_llm_freeform(QUESTION)
        self.assertEqual(res.text, _SEMANTIC_INCOHERENCE_FALLBACK)
        self.assertEqual(res.source, "llm_freeform")
        self.assertEqual(self.decisions(),
                         [{"flags": ["charset_sanity"], "source": "llm_freeform",
                           "action": "incoherence_fallback"}])


if __name__ == "__main__":
    unittest.main()
