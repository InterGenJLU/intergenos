# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A tool's result, synthesized for the person, is judged against their own turn.

The semantic-health screen reads the last user text it is handed as the turn
the reply answers: the short-nonsense rule lets a terse reply of the shape that
question asks for pass ("Paris" to "What is the capital of France?"), and the
foreign-script check exempts a reply in the script the person writes in. The
tool-synthesis request ends with the tool exchange and the synthesis
instruction, which is sent in the user role, and the screen was handed that
request whole: it read the instruction as the person's question. So a terse
synthesis was read as answering no question and flagged short_nonsense, both
attempts were rejected, and the caller served its fallback in place of a
correct answer; and the instruction's English diluted the person's own
script, so a synthesis in the language they asked in was flagged
foreign_script_flood.

These drive the real continue_after_tool_call() with only the transport stood
in. Cells marked CONTROL hold before the change and after it; every other cell
fails before it.
"""
from __future__ import annotations

import json
import unittest
import unittest.mock as mock

from intergen.interfaces.provenance import Provenance
from intergen.interfaces.types import Message, MessageRole, ToolCall
from intergen.llm import LLMRouter
from intergen.semantic_health import (
    FLAG_FOREIGN_SCRIPT, FLAG_SHORT_NONSENSE, FLAG_SYSTEM_PROMPT_ECHO,
    assess_semantic_health)

SYSTEM = ("You are InterGen, the assistant built into this operating system. "
          "Answer plainly and use the tools you are given when a question "
          "needs this machine's state or the web.")
CAPITAL = "What is the capital of France?"
LISTING = ("Search results for: capital of France\n"
           "1. Paris - Wikipedia\n"
           "   https://en.wikipedia.org/wiki/Paris\n"
           "   Paris is the capital and largest city of France.\n")


def _search_call() -> ToolCall:
    return ToolCall(name="web_search", arguments={"query": "capital of France"},
                    call_id="call_0", source_of_request=Provenance.USER_DIRECT)


def _status_call() -> ToolCall:
    return ToolCall(name="manage_services",
                    arguments={"action": "status", "service": "sshd"},
                    call_id="call_0", source_of_request=Provenance.USER_DIRECT)


class _Synthesis:
    """continue_after_tool_call() with only the transport stood in: every
    request is recorded as sent, and the model's reply to it is the next of
    the given texts (the last one repeats)."""

    def __init__(self, question, replies, *, call=None, result=LISTING,
                 history=(), success=True, executed=True):
        self.llm = LLMRouter()
        self.llm._last_finish_reason = "stop"
        self.sent = []
        self.screened = []
        replies = list(replies)
        turns = [Message(role=MessageRole.SYSTEM, content=SYSTEM)]
        turns += [Message(role=role, content=text) for role, text in history]
        turns.append(Message(role=MessageRole.USER, content=question))

        def _urlopen(req, timeout=None):
            self.sent.append(json.loads(req.data))
            return mock.MagicMock()

        def _parse(_resp):
            return iter([replies.pop(0) if len(replies) > 1 else replies[0]])

        def _spy(response_text, *, system_prompt="", conversation_texts=None):
            self.screened.append({"system_prompt": system_prompt,
                                  "conversation_texts": list(conversation_texts or [])})
            return assess_semantic_health(response_text, system_prompt=system_prompt,
                                          conversation_texts=conversation_texts)

        with mock.patch("urllib.request.urlopen", _urlopen), \
                mock.patch.object(self.llm, "_parse_sse_stream", _parse), \
                mock.patch("intergen.semantic_health.assess_semantic_health", _spy):
            self.response = self.llm.continue_after_tool_call(
                turns, call or _search_call(), result,
                success=success, executed=executed)


class ATerseSynthesisOfTheAskedShapeReachesThePersonTests(unittest.TestCase):

    def test_the_measured_case_paris_reaches_the_person(self):
        s = _Synthesis(CAPITAL, ["Paris"])
        self.assertIsNotNone(s.response, "the terse synthesis was rejected, and the "
                                         "caller would serve its fallback instead")
        self.assertEqual(s.response.text, "Paris")
        self.assertEqual(s.response.semantic_flags, [])
        self.assertEqual(len(s.sent), 1, "a passing synthesis is not asked for again")

    def test_a_yes_to_a_yes_or_no_question_reaches_the_person(self):
        s = _Synthesis("Is sshd running?", ["Yes"], call=_status_call(),
                       result="sshd.service - OpenSSH Daemon\n   Active: active (running)")
        self.assertIsNotNone(s.response)
        self.assertEqual(s.response.text, "Yes")
        self.assertEqual(s.response.semantic_flags, [])

    def test_the_screen_reads_the_persons_turn_and_never_the_instruction(self):
        # The three instructions the path appends: the tool ran, it ran and
        # exited non-zero, it did not run.
        for success, executed in ((True, True), (False, True), (False, False)):
            with self.subTest(success=success, executed=executed):
                s = _Synthesis(CAPITAL, ["Paris"],
                               success=success, executed=executed)
                instruction = s.sent[0]["messages"][-1]["content"]
                self.assertTrue(s.screened)
                for seen in s.screened:
                    self.assertEqual(seen["conversation_texts"][-1:], [CAPITAL])
                    self.assertNotIn(instruction, seen["conversation_texts"])

    def test_the_persons_earlier_turns_still_reach_the_screen(self):
        s = _Synthesis(CAPITAL, ["Paris"],
                       history=[(MessageRole.USER, "Hello there."),
                                (MessageRole.ASSISTANT, "Hi! How can I help?")])
        self.assertEqual(s.screened[0]["conversation_texts"],
                         ["Hello there.", CAPITAL])

    def test_a_synthesis_in_the_persons_own_script_is_not_a_flood(self):
        # Asked in Chinese, answered in Chinese — the measured pair, the model's
        # own answer on a local model server. The person's own turn carries the
        # script, so the flood check exempts the answer, unless the
        # instruction's English is read as part of that turn.
        s = _Synthesis("sshd 正在运行吗？", ["正在运行。"], call=_status_call(),
                       result="active")
        self.assertIsNotNone(s.response)
        self.assertEqual(s.response.text, "正在运行。")
        self.assertNotIn(FLAG_FOREIGN_SCRIPT, s.response.semantic_flags)

    # ── CONTROLS: the same before the change and after it ──

    def test_control_a_greeting_in_place_of_the_synthesis_is_still_flagged(self):
        s = _Synthesis(CAPITAL, ["Hello"])
        self.assertIsNone(s.response, "a greeting answers nothing: both attempts "
                                      "are rejected and the caller serves the tool's result")
        self.assertEqual(len(s.sent), 2)
        self.assertIn(FLAG_SHORT_NONSENSE, s.llm._last_semantic_flags)

    def test_control_the_six_word_bag_is_still_flagged(self):
        s = _Synthesis("Is sshd enabled?", ["As much least pragmatic unpaid tool"],
                       call=_status_call(), result="sshd.service: enabled")
        self.assertIsNone(s.response)
        self.assertIn(FLAG_SHORT_NONSENSE, s.llm._last_semantic_flags)

    def test_control_a_terse_reply_to_a_request_that_is_not_a_question_is_still_flagged(self):
        s = _Synthesis("Search the web for the capital of France.", ["Paris"])
        self.assertIsNone(s.response)
        self.assertIn(FLAG_SHORT_NONSENSE, s.llm._last_semantic_flags)

    def test_control_a_foreign_script_synthesis_to_an_english_question_is_still_flagged(self):
        s = _Synthesis(CAPITAL, ["巴黎是法国的首都。"])
        self.assertIsNone(s.response)
        self.assertIn(FLAG_FOREIGN_SCRIPT, s.llm._last_semantic_flags)

    def test_control_the_model_is_still_sent_the_instruction_last(self):
        s = _Synthesis(CAPITAL, ["The capital of France is Paris."])
        self.assertIsNotNone(s.response)
        messages = s.sent[0]["messages"]
        self.assertEqual([m["role"] for m in messages],
                         ["system", "user", "assistant", "tool", "user"])
        self.assertEqual(messages[1]["content"], CAPITAL)
        self.assertEqual(messages[3]["content"], LISTING)
        self.assertEqual(messages[-1]["content"],
                         s.llm._synthesis_prompt(success=True, executed=True))

    def test_control_the_live_system_prompt_still_reaches_the_echo_check(self):
        s = _Synthesis(CAPITAL, ["Paris. " + SYSTEM])
        self.assertIsNone(s.response)
        self.assertIn(FLAG_SYSTEM_PROMPT_ECHO, s.llm._last_semantic_flags)
        self.assertEqual(s.screened[0]["system_prompt"], SYSTEM)


if __name__ == "__main__":
    unittest.main()
