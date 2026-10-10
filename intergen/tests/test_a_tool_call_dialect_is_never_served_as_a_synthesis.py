# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A tool-call dialect is an artifact at the serving floor, never an answer.

THE DEFECT, read on the tree in intergen/llm.py. check_quality is the serving
floor both synthesis paths consult (the tool route's continue_after_tool_call
through _agentic_reject_reason, the fast-path summary through
_synthesis_rejection_reason), and its artifact markers named the chat-template
and reasoning tags but not the tool-call dialect's. On the tool route the
dialect is protocol, parsed before any text reaches the floor, so a reply that
still holds its tags is a call written out as prose. Measured on a local model
server through the daemon's own path: asked "What shell am I using?", the model
asked for run_command, the call was refused, and the synthesis of the refusal
was served to the person as the dialect itself.

THE ORACLE is that captured reply, verbatim: no firing produces it on demand
(it came once, on one try), so these cells hold the captured text to the floor
and to both synthesis paths, the transport stood in.

THE RULE: a reply holding the dialect's opening or closing tag is refused as
an artifact, and the floor's existing path is taken: the tool route returns no
synthesis and its caller serves the tool's own result; the fast path serves the
tool's own output.
"""
from __future__ import annotations

import unittest
import unittest.mock as mock

from intergen.interfaces.provenance import Provenance
from intergen.interfaces.types import (HardwareTierLevel, Message, MessageRole,
                                       ToolCall)
from intergen.llm import LLMRouter
from intergen.router import ConversationRouter
from intergen.semantic import SemanticMatcher
from intergen.tool_registry import ToolRegistry

QUESTION = "What shell am I using?"
# Verbatim from the synthesis served at the base of an earlier lane's
# development run (explore-run-1, try 2; its capture's line 13, "shown").
DIALECT = ("<tool_call>\n<function=run_command>\n<parameter=command>\n"
           "echo $SHELL\n</parameter>\n</function>\n</tool_call>")
REFUSED = "The command was not run: the request was refused."
SHELL_OUTPUT = "/bin/bash"


def _tool_route_synthesis(reply: str):
    """continue_after_tool_call() with only the transport stood in; the model
    replies ``reply`` to every request."""
    llm = LLMRouter()
    llm._last_finish_reason = "stop"
    turns = [Message(role=MessageRole.SYSTEM, content="You are InterGen."),
             Message(role=MessageRole.USER, content=QUESTION)]
    call = ToolCall(name="run_command", arguments={"command": "echo $SHELL"},
                    call_id="call_0", source_of_request=Provenance.USER_DIRECT)
    with mock.patch("urllib.request.urlopen", lambda req, timeout=None: mock.MagicMock()), \
            mock.patch.object(llm, "_parse_sse_stream", lambda _r: iter([reply])):
        return llm.continue_after_tool_call(turns, call, REFUSED,
                                            success=False, executed=False)


def _fast_path_summary(reply: str):
    """The router's _synthesize_tool_result with only the transport stood in."""
    reg = ToolRegistry()
    reg.discover_tools()
    r = ConversationRouter(tool_registry=reg, semantic_matcher=SemanticMatcher(embedder=None),
                           llm=LLMRouter(config=None), hardware_tier=HardwareTierLevel.TIER_2)
    r._llm._last_finish_reason = "stop"
    with mock.patch("urllib.request.urlopen", lambda req, timeout=None: mock.MagicMock()), \
            mock.patch.object(r._llm, "_parse_sse_stream", lambda _r: iter([reply])):
        text = r._synthesize_tool_result(QUESTION, "run_command", SHELL_OUTPUT)
    return text, r._last_synthesis_rejection


# ── the cells: red before the correction ────────────────────────────────────

class TheFloorRefusesTheDialect(unittest.TestCase):

    def test_the_captured_reply_is_an_artifact(self) -> None:
        self.assertEqual(LLMRouter().check_quality(DIALECT, QUESTION), "artifacts")

    def test_the_dialect_behind_a_line_of_prose_is_an_artifact(self) -> None:
        self.assertEqual(LLMRouter().check_quality(
            "Let me check that for you.\n" + DIALECT, QUESTION), "artifacts")

    def test_a_closing_tag_alone_is_an_artifact(self) -> None:
        self.assertEqual(LLMRouter().check_quality(
            "Your shell is bash.</tool_call>", QUESTION), "artifacts")


class NeitherSynthesisPathServesIt(unittest.TestCase):

    def test_the_tool_route_returns_no_synthesis(self) -> None:
        response = _tool_route_synthesis(DIALECT)
        self.assertIsNone(response, "the dialect was served as the synthesis: %r"
                          % (getattr(response, "text", response),))

    def test_the_fast_path_serves_the_tools_output(self) -> None:
        # chat() consults the same floor on each local attempt: refused twice
        # as an artifact, its ladder is spent, and the router serves the
        # tool's own output.
        text, rejection = _fast_path_summary(DIALECT)
        self.assertEqual(rejection, "quality_ladder_exhausted")
        self.assertNotIn("<tool_call>", text)
        self.assertIn(SHELL_OUTPUT, text)


# ── the controls: green at both ends ────────────────────────────────────────

class TheFloorIsOtherwiseUnchanged(unittest.TestCase):

    def test_prose_about_a_tool_call_is_served(self) -> None:
        self.assertEqual(LLMRouter().check_quality(
            "I made a tool call to check it: your shell is bash.", QUESTION), "")

    def test_the_reasoning_tags_are_still_artifacts(self) -> None:
        self.assertEqual(LLMRouter().check_quality(
            "<think>hmm</think> Your shell is bash.", QUESTION), "artifacts")

    def test_a_plain_synthesis_is_served_on_the_tool_route(self) -> None:
        response = _tool_route_synthesis("I wasn't able to run that: it was refused.")
        self.assertIsNotNone(response)
        self.assertEqual(response.text, "I wasn't able to run that: it was refused.")


if __name__ == "__main__":
    unittest.main()
