# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The fast-path tool summary is judged against the person's own turn.

THE DEFECT, read on the tree in intergen/router.py. _synthesize_tool_result
(the keyword and semantic routes' summary of a tool's result, and the tool
route's fallback) asks the model through chat() with one user message: a prompt
that embeds the person's question ("The user asked: ..."), the tool's data and
the rules. chat() screens every generation against the request it sends, so the
semantic-health screen read that prompt as the person's question. A terse
summary of the shape the question asks for ("Paris" to "What is the capital of
France?", "Yes" to "Is sshd running?") was read as answering nothing and
flagged short_nonsense, and the router served the tool's raw output in place of
the correct answer; and the prompt's English outweighed a question asked in
Chinese, so the model's correct answer in Chinese was flagged
foreign_script_flood. The class corrected for the tool-synthesis request in
LLMRouter._the_turn_before_the_tool_exchange.

THE RULE: the screen on this path judges the summary against the person's own
turn; the request sent to the model is unchanged. The instruction the prompt
carries is handed to the screen as instruction text (the system role), so a
summary that says the rules back is flagged as an echo of them: left out of
the screen, two rules said back verbatim were served as the answer.

These drive the real _synthesize_tool_result, chat(), stream() and the screen
with only the transport stood in: every request is recorded as sent, and the
model's reply is given. Cells marked CONTROL hold before the change and after.
"""
from __future__ import annotations

import json
import unittest
import unittest.mock as mock

from intergen.interfaces.types import HardwareTierLevel
from intergen.llm import LLMRouter
from intergen.router import ConversationRouter
from intergen.semantic import SemanticMatcher
from intergen.semantic_health import (
    FLAG_FOREIGN_SCRIPT, FLAG_SHORT_NONSENSE, assess_semantic_health)
from intergen.tool_registry import ToolRegistry

CAPITAL = "What is the capital of France?"
LISTING = ("Search results for: capital of France\n"
           "1. Paris - Wikipedia\n"
           "   https://en.wikipedia.org/wiki/Paris\n"
           "   Paris is the capital and largest city of France.\n")
STATUS = "sshd.service - OpenSSH Daemon\n   Active: active (running)"
LOOP = "the the the the the the the the the the the the the the the the the the"
RULES_BACK = ("Jump straight into the answer. No preamble.\n"
              "Be concise. State the facts from the tool output.")


class _FastPath:
    """One fast-path summary through the router the daemon builds, with only
    the transport stood in."""

    def __init__(self, question: str, reply: str, *, tool: str = "web_search",
                 output: str = LISTING) -> None:
        reg = ToolRegistry()
        reg.discover_tools()
        tier = HardwareTierLevel.TIER_2
        self.router = ConversationRouter(
            tool_registry=reg, semantic_matcher=SemanticMatcher(embedder=None),
            llm=LLMRouter(config=None), hardware_tier=tier)
        llm = self.router._llm
        llm._last_finish_reason = "stop"
        self.sent: list[dict] = []
        self.screened: list[dict] = []

        def _urlopen(req, timeout=None):
            self.sent.append(json.loads(req.data))
            return mock.MagicMock()

        def _parse(_resp):
            return iter([reply])

        def _spy(response_text, *, system_prompt="", conversation_texts=None):
            self.screened.append({"system_prompt": system_prompt,
                                  "conversation_texts": list(conversation_texts or [])})
            return assess_semantic_health(response_text, system_prompt=system_prompt,
                                          conversation_texts=conversation_texts)

        with mock.patch("urllib.request.urlopen", _urlopen), \
                mock.patch.object(llm, "_parse_sse_stream", _parse), \
                mock.patch("intergen.semantic_health.assess_semantic_health", _spy):
            self.text = self.router._synthesize_tool_result(question, tool, output)
        self.rejection = self.router._last_synthesis_rejection


# ── the cells: red before the correction ────────────────────────────────────

class ATerseSummaryOfTheAskedShapeReachesThePerson(unittest.TestCase):

    def test_paris_to_the_capital_of_france(self) -> None:
        s = _FastPath(CAPITAL, "Paris")
        self.assertIsNone(s.rejection, "the summary was rejected (%s) and the "
                          "person was served the tool's raw output" % s.rejection)
        self.assertEqual(s.text, "Paris")

    def test_yes_to_is_sshd_running(self) -> None:
        s = _FastPath("Is sshd running?", "Yes", tool="manage_services", output=STATUS)
        self.assertIsNone(s.rejection, s.rejection)
        self.assertEqual(s.text, "Yes")


class ASummaryInThePersonsOwnScriptIsNotAFlood(unittest.TestCase):

    def test_an_answer_in_chinese_to_a_question_in_chinese(self) -> None:
        s = _FastPath("sshd 正在运行吗？", "正在运行。", tool="manage_services",
                      output=STATUS)
        self.assertIsNone(s.rejection, s.rejection)
        self.assertEqual(s.text, "正在运行。")


class TheScreenReadsThePersonsTurn(unittest.TestCase):

    def test_the_screen_is_handed_the_persons_turn_and_never_the_prompt(self) -> None:
        s = _FastPath(CAPITAL, "Paris")
        prompt = s.sent[0]["messages"][-1]["content"]
        self.assertTrue(s.screened, "the screen did not run")
        for seen in s.screened:
            self.assertEqual(seen["conversation_texts"], [CAPITAL])
            self.assertNotIn(prompt, seen["conversation_texts"])


class ASummaryThatSaysTheRulesBackIsNotServed(unittest.TestCase):

    def test_two_rules_said_back_verbatim(self) -> None:
        s = _FastPath(CAPITAL, RULES_BACK)
        self.assertIsNotNone(s.rejection, "the rules said back were served as the answer")
        self.assertIn("system_prompt_echo", s.rejection)
        self.assertNotEqual(s.text.strip(), RULES_BACK)


# ── the controls: green at both ends ────────────────────────────────────────

class ASummaryThatQuotesTheToolsDataIsServed(unittest.TestCase):
    """CONTROL: the tool's own words are not instruction text."""

    def test_a_sentence_taken_from_the_listing(self) -> None:
        s = _FastPath(CAPITAL, "Paris is the capital and largest city of France.")
        self.assertIsNone(s.rejection, s.rejection)
        self.assertEqual(s.text, "Paris is the capital and largest city of France.")


class TheRequestSentIsUnchanged(unittest.TestCase):
    """CONTROL: the model is still asked with the synthesis prompt."""

    def test_the_request_carries_the_prompt_and_the_tools_data(self) -> None:
        s = _FastPath(CAPITAL, "Paris")
        self.assertEqual(len(s.sent), 1)
        last = s.sent[0]["messages"][-1]
        self.assertEqual(last["role"], "user")
        self.assertTrue(last["content"].startswith('The user asked: "%s"' % CAPITAL),
                        last["content"][:120])
        self.assertIn("Paris is the capital and largest city of France.",
                      last["content"])


class ACorruptSummaryIsStillRejected(unittest.TestCase):
    """CONTROL: the screen still refuses a summary that is not language."""

    def test_a_loop_is_rejected_and_the_tools_output_is_served(self) -> None:
        s = _FastPath(CAPITAL, LOOP)
        self.assertIsNotNone(s.rejection)
        self.assertNotEqual(s.text, LOOP)
        self.assertIn("Paris is the capital and largest city of France.", s.text)


if __name__ == "__main__":
    unittest.main()
