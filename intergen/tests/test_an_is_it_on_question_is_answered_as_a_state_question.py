# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A question whether something is on is answered as a state question.

THE DEFECT, read on the tree in intergen/router.py. The router's state-question
reader knows lead-ins such as "how much" and "what's my", and a yes-or-no
question about whether something on this machine is on carries none of them.
So "Is bluetooth on?" and "Is my firewall on?" were not read as state questions,
and the explain gate answered them with a curated how-to: on the corpus's
keyword path the page on connecting bluetooth headphones and the firewall page,
and on its embedding path the page for bluetooth that is not working. The
person asked about this machine's current state and was taught a procedure.

THE RULE these cells hold the router to: "is X on", "are my X off", "is the X
enabled" and their "turned on" and "switched off" forms are state questions. The
explain gate does not serve them a how-to, whatever the corpus scores, and the
turn goes on to the routes that read the machine. A pronoun subject ("is it
on?") names nothing on the machine and is not read as one.

THE CONTROLS beside the cells are green at both ends: a how-to question about
turning something on is still taught, "is it on?" and "what is bluetooth?" are
not state questions, and "what's my hostname?" still is.

The router is built the way the daemon builds it with no embedding server, on
the tree's own how-to corpus. The model is stubbed at both entry points and
counted with the request it was asked, the tool registry's dispatch is stood in
and recorded, and every way a turn can leave the process (a child process, a
socket, urllib) is recorded and refused for the turn.
"""
from __future__ import annotations

import os
import socket
import unittest
from unittest import mock

from intergen import howto
from intergen.interfaces.types import HardwareTierLevel, ToolResult
from intergen.llm import LLMRouter
from intergen.router import ConversationRouter
from intergen.semantic import SemanticMatcher
from intergen.tool_registry import ToolRegistry

MODEL_ANSWER = "(a model's answer)"
NAMED = ("Is bluetooth on?", "Is my firewall on?")
SHAPES = NAMED + ("Is wifi off?", "Is the firewall enabled?", "Is bluetooth turned on?",
                  "Are my speakers on?", "Is my vpn on?")


class _BoundaryReached(BaseException):
    """Deliberately NOT an Exception: the model client's ``except Exception``
    paths would swallow an ordinary one and the case would walk on with the
    request already made."""


class _Resp:
    """A completion carrying only what the free-form path reads."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.quality_passed = True
        self.escalated = False
        self.local = True
        self.model = "stub"
        self.tokens_prompt = 0
        self.tokens_completion = 0
        self.semantic_flags = []


def _last_user(messages) -> str:
    """The text of the last user message a model request carried."""
    for m in reversed(list(messages or [])):
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
        if getattr(role, "value", role) == "user":
            content = (m.get("content") if isinstance(m, dict)
                       else getattr(m, "content", ""))
            return content if isinstance(content, str) else repr(content)
    return ""


class _Case(unittest.TestCase):
    """The router with no embedding server, and one turn."""

    def _router(self) -> ConversationRouter:
        from intergen.intents import register_all_intents
        reg = ToolRegistry()
        reg.discover_tools()
        self.dispatched: list[tuple[str, dict]] = []

        def _execute(call, *a, **kw):
            self.dispatched.append((call.name, dict(call.arguments or {})))
            return ToolResult(call_id=call.call_id, name=call.name,
                              content="(the stand-in's output)", success=True)

        reg.execute = _execute
        matcher = SemanticMatcher(embedder=None)
        register_all_intents(matcher)
        with mock.patch.dict(os.environ, {"INTERGEN_HOWTO_DIR": str(howto._REPO_DIR)}):
            r = ConversationRouter(
                tool_registry=reg, semantic_matcher=matcher, llm=LLMRouter(config=None),
                lock_dispatch=False, hardware_tier=HardwareTierLevel.TIER_2)
        self.assertIsNotNone(r._howto, "control: the router built no how-to corpus")
        self.asked: list[tuple[str, str]] = []

        def _chat(messages, **kw):
            self.asked.append(("chat", _last_user(messages)))
            return _Resp(MODEL_ANSWER)

        def _stream_with_tools(messages, *, tools=(), **kw):
            self.asked.append(("stream_with_tools", _last_user(messages)))
            yield MODEL_ANSWER

        r._llm.chat = _chat
        r._llm.stream_with_tools = _stream_with_tools
        return r

    def _turn(self, r: ConversationRouter, request: str):
        self.processes: list[str] = []
        self.connections: list[str] = []
        self.urls: list[str] = []

        def _popen(args, *a, **kw):
            self.processes.append(args if isinstance(args, str)
                                  else " ".join(map(str, args)))
            raise OSError("a child process, refused by this case")

        def _connect(sock, address, *a, **kw):
            self.connections.append(repr(address))
            raise OSError("a connection, refused by this case")

        def _urlopen(req, *a, **kw):
            self.urls.append(getattr(req, "full_url", repr(req)))
            raise _BoundaryReached(self.urls[-1])

        with mock.patch("subprocess.Popen", _popen), \
                mock.patch.object(socket.socket, "connect", _connect), \
                mock.patch("urllib.request.urlopen", _urlopen):
            return r.route(request)


# ── the cells: red before the correction ────────────────────────────────────

class TheReaderReadsTheShapeAsAStateQuestion(_Case):

    def test_every_is_it_on_shape(self) -> None:
        r = self._router()
        for question in SHAPES:
            with self.subTest(question=question):
                self.assertTrue(r._looks_like_state_question(question.lower()))


class TheExplainGateServesNoHowToWhateverTheCorpusScores(_Case):

    def test_the_two_named_questions(self) -> None:
        r = self._router()
        entry = next(e for e in r._howto._entries if e.id == "net-firewall")
        for question in NAMED:
            with self.subTest(question=question):
                with mock.patch.object(r._howto, "retrieve", return_value=(entry, 1.0)):
                    result, _prior = r._try_explain(question)
                self.assertIsNone(result, "%r was served a how-to" % question)


class OnTheKeywordPathTheTurnGoesOnToTheModel(_Case):

    def test_the_two_named_questions(self) -> None:
        for question in NAMED:
            with self.subTest(question=question):
                r = self._router()
                result = self._turn(r, question)
                self.assertNotEqual(result.source, "explain",
                                    "%r was answered with a how-to: %r"
                                    % (question, result.text))
                self.assertEqual(self.asked, [("stream_with_tools", question)])
                self.assertEqual((self.processes, self.connections, self.urls),
                                 ([], [], []))


# ── the controls: green at both ends ────────────────────────────────────────

class WhatIsNotAStateQuestionStaysOne(_Case):

    def test_a_how_to_about_turning_something_on_is_still_taught(self) -> None:
        r = self._router()
        result = self._turn(r, "How do I turn bluetooth on?")
        self.assertEqual(result.source, "explain")
        self.assertEqual(self.asked, [])

    def test_is_it_on_and_what_is_bluetooth_are_not_state_questions(self) -> None:
        r = self._router()
        for question in ("Is it on?", "What is bluetooth?", "Is that enabled?"):
            with self.subTest(question=question):
                self.assertFalse(r._looks_like_state_question(question.lower()))

    def test_whats_my_hostname_still_is(self) -> None:
        r = self._router()
        self.assertTrue(r._looks_like_state_question("what's my hostname?"))


if __name__ == "__main__":
    unittest.main()
