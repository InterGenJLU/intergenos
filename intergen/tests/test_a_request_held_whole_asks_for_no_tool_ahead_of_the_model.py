# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A request held whole asks for no tool ahead of the model.

THE DEFECT, read on the tree in intergen/router.py. A request the decomposer
would split is held whole and handed to the model in one turn when it carries a
forget and there is no memory store to hand it to (route_compound_whole). The
turn's keyword route was still asked first, and only its answer was set aside:
``_try_keyword_match`` dispatches its tool and writes its answer to the
conversation before it returns, so "forget my car and what time is it" asked
for ``date`` as the person's own request, recorded it in the dispatch record,
and left the time as the turn's reply in the conversation, while the person was
answered by the model. "what's my hostname", "what kernel am i running", "what
cpu do i have" and "how much free space" in the same place did the same.

THE RULE these cells hold the router to: a request held whole reaches the model
with no keyword-route dispatch ahead of it. No tool is asked for, and nothing is
written to the conversation but the model's reply.

THE CONTROLS beside the cells are green at both ends: a request that is not
held whole still takes the keyword route ("what kernel am i running" on its
own), a compound with no forget is still split and its clause still takes the
keyword route, and with a memory store the forget-carrying request is still
answered by the memory slot.

Every case builds the router the way the daemon builds it. The model is stubbed
at both entry points and counted with the request it was asked; the tool
registry's dispatch is stood in, recording each call it is asked for and running
nothing; and every way a turn can leave the process (a child process, a socket,
urllib) is recorded and refused for the turn.
"""
from __future__ import annotations

import os
import socket
import tempfile
import unittest
from unittest import mock

from intergen.decomposer import analyze_query
from intergen.interfaces.types import HardwareTierLevel, ToolResult
from intergen.llm import LLMRouter
from intergen.memory import MemoryManager
from intergen.router import ConversationRouter
from intergen.semantic import SemanticMatcher
from intergen.tool_registry import ToolRegistry

MODEL_ANSWER = "(a model's answer)"
STAND_IN_OUTPUT = "(the stand-in's output)"
# The held-whole shapes the keyword route carries, each measured asking for its
# tool before this correction.
HELD_WHOLE = (
    "forget my car and what's the time",
    "forget my car and what's my hostname",
    "forget my car and what kernel am i running",
    "forget my car and what cpu do i have",
    "forget my car and how much free space",
)


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
    """The router, the stood-in dispatch and one turn, as every case below uses
    them."""

    def _router(self, mm: MemoryManager | None = None) -> ConversationRouter:
        """A router built the way the daemon builds it: tools discovered, intents
        registered; the registry's dispatch stood in and recorded."""
        from intergen.intents import register_all_intents
        reg = ToolRegistry()
        reg.discover_tools()
        self.dispatched: list[tuple[str, dict]] = []

        def _execute(call, *a, **kw):
            self.dispatched.append((call.name, dict(call.arguments or {})))
            return ToolResult(call_id=call.call_id, name=call.name,
                              content=STAND_IN_OUTPUT, success=True)

        reg.execute = _execute
        matcher = SemanticMatcher(embedder=None)
        register_all_intents(matcher)
        r = ConversationRouter(
            tool_registry=reg, semantic_matcher=matcher, llm=LLMRouter(config=None),
            lock_dispatch=False, hardware_tier=HardwareTierLevel.TIER_2, memory=mm)
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
        """One turn through route(), every way out of the process recorded and
        refused for its length."""
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

    @staticmethod
    def _replies(r: ConversationRouter) -> list[str]:
        """What the conversation holds as the assistant's replies."""
        return [m.content for m in r._conv.history
                if getattr(m.role, "value", m.role) == "assistant"]

    def assertGoesToTheModelWithNoToolAhead(self, request: str) -> None:
        """The cell's outcome: held whole, the request reaches the model once,
        with nothing asked of the tool registry ahead of it and no reply but the
        model's in the conversation; nothing run or reached."""
        self.assertTrue(
            analyze_query(request, HardwareTierLevel.TIER_2).needs_decomposition,
            "control: the decomposer no longer reads %r as two asks, so this "
            "cell would measure a request that is never held whole" % request)
        self.assertIsNotNone(
            MemoryManager.is_forget_request(request),
            "control: the forget parser finds no forget in %r" % request)
        r = self._router(None)
        result = self._turn(r, request)
        self.assertNotIn(result.source, ("decomposed", "keyword"),
                         "%r was not held whole: %r" % (request, result.source))
        self.assertEqual(self.dispatched, [],
                         "%r asked for a tool ahead of the model" % request)
        self.assertEqual(self.asked, [("stream_with_tools", request)],
                         "%r was not answered by the model whole, once" % request)
        self.assertEqual(
            [t for t in self._replies(r) if t != MODEL_ANSWER], [],
            "%r left a reply in the conversation that the model did not give"
            % request)
        self.assertEqual((self.processes, self.connections, self.urls), ([], [], []),
                         "%r ran or reached something" % request)


# ── the cells: red before the correction ────────────────────────────────────

class ARequestHeldWholeAsksForNoToolAheadOfTheModel(_Case):
    """No memory store: the request is held whole, and the keyword route is not
    asked."""

    def test_forget_my_car_and_what_time_is_it(self) -> None:
        """The captured shape: ``date`` was asked for as the person's request."""
        self.assertGoesToTheModelWithNoToolAhead("forget my car and what time is it")

    def test_every_held_whole_shape_the_keyword_route_carries(self) -> None:
        for request in HELD_WHOLE:
            with self.subTest(request=request):
                self.assertGoesToTheModelWithNoToolAhead(request)


# ── the controls: green at both ends ────────────────────────────────────────

class TheKeywordRouteStillCarriesWhatIsNotHeldWhole(_Case):

    def test_what_kernel_am_i_running_on_its_own(self) -> None:
        request = "what kernel am i running"
        self.assertFalse(analyze_query(request, HardwareTierLevel.TIER_2).needs_decomposition)
        r = self._router(None)
        result = self._turn(r, request)
        self.assertEqual(result.source, "keyword")
        self.assertEqual(self.dispatched, [("run_command", {"command": "uname -r"})])
        self.assertIn(STAND_IN_OUTPUT, result.text)
        self.assertEqual(self.asked, [])

    def test_a_compound_with_no_forget_is_split_and_its_clause_takes_the_keyword_route(self) -> None:
        request = "what time is it and tell me a joke"
        self.assertIsNone(MemoryManager.is_forget_request(request))
        r = self._router(None)
        result = self._turn(r, request)
        self.assertEqual(result.source, "decomposed")
        self.assertEqual(self.dispatched, [("run_command", {"command": "date"})])

    def test_with_a_memory_store_the_forget_carrying_request_is_the_memory_slots(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="held-whole-")
        self.addCleanup(tmp.cleanup)
        mm = MemoryManager(os.path.join(tmp.name, "memory.db"))
        mm.store("car", "a red hatchback")
        r = self._router(mm)
        result = self._turn(r, "forget my car and what time is it")
        self.assertEqual((result.source, result.handled), ("memory", True))
        self.assertEqual(self.dispatched, [])
        self.assertEqual(self.asked, [])


if __name__ == "__main__":
    unittest.main()
