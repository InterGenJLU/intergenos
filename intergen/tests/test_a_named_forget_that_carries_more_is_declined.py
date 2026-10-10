# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A named forget whose request carries more than the forget is declined.

THE DEFECT, read on the tree in intergen/memory.py. The decomposer reads "tell
me a joke and forget my car" and "forget my car and my editor" as ONE request,
so each reaches the memory slot whole, and the forget parser read only the part
its pattern matched. The first forgot the car and the joke was dropped without a
word; the second took "my car and my editor" as one subject and answered that
there was no such memory, saying nothing of either thing it named.

THE RULE these cells hold the parser and the memory slot to: a named forget is
carried out only when it is the whole request. With a clause in front of it, or
a second subject joined to the first, it is declined: nothing is forgotten, and
the reply names what was not done and asks for the forget on its own.

THE CONTROLS beside the cells are green at both ends: a forget on its own still
forgets, behind a lead, the assistant's name or a plain request frame; the reset
phrase is unchanged, and a request carrying it with more is still declined with
the reply it had.

Every case builds the router the way the daemon builds it, on its own
throwaway store; the model is stubbed and counted, and every way a turn can
leave the process (a child process, a socket, urllib) is recorded and refused.
"""
from __future__ import annotations

import os
import socket
import sqlite3
import tempfile
import unittest
from unittest import mock

from intergen.decomposer import analyze_query
from intergen.dispatch_policy import DispatchMode, resolve_dispatch_for_model
from intergen.interfaces.types import HardwareTierLevel
from intergen.llm import LLMRouter
from intergen.memory import MemoryManager
from intergen.router import ConversationRouter
from intergen.semantic import SemanticMatcher
from intergen.tool_registry import ToolRegistry

STORED = (("car", "a red hatchback"), ("editor", "vim"))


class _BoundaryReached(BaseException):
    """Deliberately NOT an Exception, so no ``except Exception`` swallows it."""


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


class _Case(unittest.TestCase):
    """The router, the store and one turn, as every case below uses them."""

    def _store(self) -> MemoryManager:
        tmp = tempfile.TemporaryDirectory(prefix="forget-more-")
        self.addCleanup(tmp.cleanup)
        mm = MemoryManager(os.path.join(tmp.name, "memory.db"))
        for key, value in STORED:
            mm.store(key, value)
        self.assertEqual(self._live_keys(mm), ["car", "editor"],
                         "control: the facts were not both stored")
        return mm

    @staticmethod
    def _live_keys(mm: MemoryManager) -> list[str]:
        """The facts still held, asked of the database rather than of the API."""
        conn = sqlite3.connect(str(mm._db_path))
        try:
            return sorted(k for (k,) in conn.execute(
                "SELECT key FROM facts WHERE deleted = 0"))
        finally:
            conn.close()

    def _router(self, mm: MemoryManager) -> ConversationRouter:
        """A router built the way the daemon builds it."""
        from intergen.intents import register_all_intents
        tier = HardwareTierLevel.TIER_2
        reg = ToolRegistry()
        reg.discover_tools()
        matcher = SemanticMatcher(embedder=None)
        register_all_intents(matcher)
        locked = resolve_dispatch_for_model(
            tier, detected_tier=tier).dispatch_mode is DispatchMode.LOCKED_DOWN
        r = ConversationRouter(
            tool_registry=reg, semantic_matcher=matcher, llm=LLMRouter(config=None),
            lock_dispatch=locked, hardware_tier=tier, memory=mm)
        self.model_calls: list[str] = []

        def _chat(messages, **kw):
            self.model_calls.append("chat")
            return _Resp("(a model's answer)")

        def _stream_with_tools(messages, *, tools=(), **kw):
            self.model_calls.append("stream_with_tools")
            yield "(a model's answer)"

        r._llm.chat = _chat
        r._llm.stream_with_tools = _stream_with_tools
        return r

    def _turn(self, r: ConversationRouter, request: str):
        """One turn through route(), every way out of the process refused."""
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

    def assertDeclinedByName(self, request: str, *, says) -> None:
        """The cell's outcome, through the memory slot on one turn: the reply
        declines, names what was not done, and nothing is forgotten."""
        self.assertFalse(
            analyze_query(request, HardwareTierLevel.TIER_2).needs_decomposition,
            "control: the decomposer now splits %r, so it would no longer reach "
            "the memory slot whole" % request)
        mm = self._store()
        result = self._turn(self._router(mm), request)
        self.assertEqual(result.source, "memory",
                         "%r was answered by %r: %r" % (request, result.source, result.text))
        self.assertTrue(result.text.startswith("I haven't forgotten"),
                        "%r was not declined: %r" % (request, result.text))
        for words in says:
            self.assertIn(words, result.text,
                          "%r: the reply does not name %r: %r" % (request, words, result.text))
        self.assertEqual(self._live_keys(mm), ["car", "editor"],
                         "%r forgot a fact on a reading of part of it" % request)
        self.assertEqual(self.model_calls, [], "%r reached the model" % request)
        self.assertEqual((self.processes, self.connections, self.urls), ([], [], []),
                         "%r ran or reached something" % request)


# ── the cells: red before the correction ────────────────────────────────────

class AForgetWithAClauseBesideItIsDeclined(_Case):
    """A clause in front of the forget: nothing forgotten, the clause named."""

    def test_tell_me_a_joke_and_forget_my_car(self) -> None:
        self.assertDeclinedByName("tell me a joke and forget my car",
                                  says=['"my car"', '"tell me a joke"'])


class AForgetNamingTwoThingsIsDeclined(_Case):
    """A second subject joined to the first: nothing forgotten, both named."""

    def test_forget_my_car_and_my_editor(self) -> None:
        self.assertDeclinedByName("forget my car and my editor",
                                  says=['"my car"', '"my editor"'])

    def test_delete_the_memory_about_my_car_and_my_editor(self) -> None:
        self.assertDeclinedByName("delete the memory about my car and my editor",
                                  says=['"my car"', '"my editor"'])

    def test_forget_my_car_comma_my_editor(self) -> None:
        self.assertDeclinedByName("forget my car, my editor",
                                  says=['"my car"', '"my editor"'])


# ── the controls: green at both ends ────────────────────────────────────────

class AForgetOnItsOwnStillForgets(_Case):
    """The forget alone, behind a lead, the assistant's name or a plain frame."""

    def test_each_form_forgets_the_car(self) -> None:
        for request in ("forget my car", "please forget about my car",
                        "can you please forget my car?", "InterGen, forget my car",
                        "I want you to forget my car",
                        "go ahead and forget my car, thanks"):
            with self.subTest(request=request):
                mm = self._store()
                result = self._turn(self._router(mm), request)
                self.assertEqual(
                    (result.source, result.text),
                    ("memory", "Done. I've forgotten 1 thing about 'my car'."))
                self.assertEqual(self._live_keys(mm), ["editor"])
                self.assertEqual(self.model_calls, [])


class TheResetPhraseIsUnchanged(_Case):
    """The reset on its own clears; with more beside it, the decline it had."""

    def test_clear_all_my_memories(self) -> None:
        mm = self._store()
        result = self._turn(self._router(mm), "clear all my memories")
        self.assertEqual((result.source, result.text),
                         ("memory", "Done. I've cleared all 2 memories."))
        self.assertEqual(self._live_keys(mm), [])

    def test_forget_my_editor_and_clear_all_my_memories(self) -> None:
        mm = self._store()
        result = self._turn(self._router(mm),
                            "forget my editor and clear all my memories")
        self.assertEqual(result.source, "memory")
        self.assertTrue(result.text.startswith(
            "I haven't forgotten or cleared anything: I couldn't tell exactly "
            "what to forget from that."), result.text)
        self.assertEqual(self._live_keys(mm), ["car", "editor"])


if __name__ == "__main__":
    unittest.main()
