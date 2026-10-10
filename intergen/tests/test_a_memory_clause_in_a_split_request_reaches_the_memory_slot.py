# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A remember or recall clause in a split request reaches the memory slot.

THE DEFECT, read on the tree in intergen/router.py. A request the decomposer
splits is routed clause by clause through ``_route_single``, and the comment at
``_COMPOUND_MEMORY_RE`` says a memory clause keeps the compound decomposed so
that its recall reaches the memory slot. ``_route_single`` had no memory rung,
so it never did: "remember that my editor is vim and tell me a joke" stored
nothing and handed the remember clause to a tool-calling model turn, and "what
is my name and tell me a joke" asked the model for the name the store held.

THE RULE these cells hold the router to: with a memory store, a clause the
compound-memory pattern marks is answered by the memory slot and never by a
model turn. A remember clause is stored and the reply says what was kept; a
recall clause is answered from the store, and when the store holds nothing the
clause names the reply says so. The other clause routes as before. A forget
never reaches a clause: a request that carries one is held whole before any
split (the cells of test_a_forget_request_reaches_the_memory_slot_whole).

THE CONTROLS beside the cells are green at both ends: a remember or a recall
asked on its own is answered by the memory slot as before, and with no memory
store there is no slot, so the clause goes on to the model as before.

Every case builds the router the way the daemon builds it, on its own
throwaway store. The model is stubbed at both entry points and counted with
the request it was asked, and every way a turn can leave the process (a child
process, a socket, urllib) is recorded and refused for the turn.
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

JOKE = "tell me a joke"


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
    """The router, the store and one turn, as every case below uses them."""

    def _store(self, facts=()) -> MemoryManager:
        """A store of its own, holding exactly ``facts``."""
        tmp = tempfile.TemporaryDirectory(prefix="memory-clause-")
        self.addCleanup(tmp.cleanup)
        mm = MemoryManager(os.path.join(tmp.name, "memory.db"))
        for key, value in facts:
            mm.store(key, value)
        self.assertEqual(
            self._live(mm), dict(facts),
            "control: the store does not hold what the case put in it")
        return mm

    @staticmethod
    def _live(mm: MemoryManager) -> dict:
        """The facts still held, asked of the database rather than of the API."""
        conn = sqlite3.connect(str(mm._db_path))
        try:
            return dict(conn.execute(
                "SELECT key, value FROM facts WHERE deleted = 0"))
        finally:
            conn.close()

    def _router(self, mm: MemoryManager | None,
                tier: HardwareTierLevel = HardwareTierLevel.TIER_2) -> ConversationRouter:
        """A router built the way the daemon builds it: tools discovered, intents
        registered, the dispatch posture asked of the product's resolver."""
        from intergen.intents import register_all_intents
        reg = ToolRegistry()
        reg.discover_tools()
        matcher = SemanticMatcher(embedder=None)
        register_all_intents(matcher)
        locked = resolve_dispatch_for_model(
            tier, detected_tier=tier).dispatch_mode is DispatchMode.LOCKED_DOWN
        r = ConversationRouter(
            tool_registry=reg, semantic_matcher=matcher, llm=LLMRouter(config=None),
            lock_dispatch=locked, hardware_tier=tier, memory=mm)
        self.asked: list[tuple[str, str]] = []

        def _chat(messages, **kw):
            self.asked.append(("chat", _last_user(messages)))
            return _Resp("(a model's answer)")

        def _stream_with_tools(messages, *, tools=(), **kw):
            self.asked.append(("stream_with_tools", _last_user(messages)))
            yield "(a model's answer)"

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

    def _split(self, request: str, memory_clause: str) -> None:
        """Control: the decomposer still splits the request, and the memory
        clause is one of its clauses."""
        d = analyze_query(request, HardwareTierLevel.TIER_2)
        self.assertTrue(
            d.needs_decomposition,
            "control: the decomposer no longer splits %r, so this cell would "
            "measure a request no clause router sees" % request)
        self.assertIn(memory_clause, d.sub_queries,
                      "control: %r is not a clause of %r" % (memory_clause, request))

    def assertAnsweredByTheSlot(self, request: str, memory_clause: str, *,
                                facts, says, holds_after) -> None:
        """The cell's outcome: the decomposed turn's reply carries the memory
        slot's answer, the store holds ``holds_after``, and the model was asked
        once, for the other clause only; nothing run or reached."""
        self._split(request, memory_clause)
        mm = self._store(facts)
        result = self._turn(self._router(mm), request)
        self.assertEqual((result.source, result.handled), ("decomposed", True),
                         "%r was not answered clause by clause: %r"
                         % (request, result.text))
        for words in says:
            self.assertIn(words, result.text,
                          "%r: the reply does not say %r: %r"
                          % (request, words, result.text))
        self.assertEqual(self._live(mm), dict(holds_after),
                         "%r: the store holds the wrong facts" % request)
        self.assertEqual(
            [q for _e, q in self.asked], [JOKE],
            "%r: the model was asked %r, not the joke alone" % (request, self.asked))
        self.assertEqual((self.processes, self.connections, self.urls), ([], [], []),
                         "%r ran or reached something" % request)


# ── the cells: red before the correction ────────────────────────────────────

class ARememberClauseIsStored(_Case):
    """A remember clause is kept in the store, and the reply says what was kept."""

    def test_remember_that_my_editor_is_vim_and_tell_me_a_joke(self) -> None:
        self.assertAnsweredByTheSlot(
            "remember that my editor is vim and tell me a joke",
            "remember that my editor is vim",
            facts=[("name", "Ada")],
            says=["I'll remember", "editor", "vim"],
            holds_after=[("name", "Ada"), ("editor", "vim")])


class ARecallClauseIsAnsweredFromTheStore(_Case):
    """A recall clause is answered from the store, never asked of the model."""

    def test_what_is_my_name_and_tell_me_a_joke(self) -> None:
        self.assertAnsweredByTheSlot(
            "what is my name and tell me a joke", "what is my name",
            facts=[("name", "Ada")], says=["Ada"],
            holds_after=[("name", "Ada")])

    def test_tell_me_a_joke_and_what_is_my_name(self) -> None:
        self.assertAnsweredByTheSlot(
            "tell me a joke and what is my name", "what is my name",
            facts=[("name", "Ada")], says=["Ada"],
            holds_after=[("name", "Ada")])

    def test_do_you_remember_my_editor_and_tell_me_a_joke(self) -> None:
        self.assertAnsweredByTheSlot(
            "do you remember my editor and tell me a joke", "do you remember my editor",
            facts=[("editor", "vim")], says=["vim"],
            holds_after=[("editor", "vim")])

    def test_recall_my_editor_and_tell_me_a_joke(self) -> None:
        self.assertAnsweredByTheSlot(
            "recall my editor and tell me a joke", "recall my editor",
            facts=[("editor", "vim")], says=["vim"],
            holds_after=[("editor", "vim")])

    def test_a_recall_the_store_cannot_answer_says_so(self) -> None:
        self.assertAnsweredByTheSlot(
            "what is my name and tell me a joke", "what is my name",
            facts=[("editor", "vim")], says=["nothing stored"],
            holds_after=[("editor", "vim")])

    def test_a_recall_over_a_store_that_cannot_be_read_says_so(self) -> None:
        request = "what is my name and tell me a joke"
        self._split(request, "what is my name")
        mm = self._store([("name", "Ada")])
        r = self._router(mm)
        with mock.patch.object(mm, "list_all",
                               side_effect=sqlite3.OperationalError("disk I/O error")):
            result = self._turn(r, request)
        self.assertEqual((result.source, result.handled), ("decomposed", True), result.text)
        self.assertIn("couldn't read what I have stored", result.text)
        self.assertNotIn("nothing stored", result.text)
        self.assertEqual([q for _e, q in self.asked], [JOKE],
                         "the model was asked %r, not the joke alone" % self.asked)


# ── the controls: green at both ends ────────────────────────────────────────

class AMemoryRequestOnItsOwnIsUnchanged(_Case):
    """Asked on its own, a remember or a recall is the memory slot's, as before."""

    def test_remember_on_its_own_is_stored(self) -> None:
        mm = self._store([("name", "Ada")])
        result = self._turn(self._router(mm), "remember that my editor is vim")
        self.assertEqual((result.source, result.handled), ("memory", True), result.text)
        self.assertEqual(self._live(mm), {"name": "Ada", "editor": "vim"})
        self.assertEqual(self.asked, [])

    def test_a_recall_on_its_own_is_answered_from_the_store(self) -> None:
        mm = self._store([("name", "Ada")])
        result = self._turn(self._router(mm), "what is my name")
        self.assertEqual((result.source, result.handled), ("memory", True), result.text)
        self.assertIn("Ada", result.text)
        self.assertEqual(self.asked, [])


class WithNoStoreThereIsNoSlot(_Case):
    """With no memory store the clause goes on to the model, as before."""

    def test_the_remember_clause_goes_on_to_the_model(self) -> None:
        request = "remember that my editor is vim and tell me a joke"
        self._split(request, "remember that my editor is vim")
        result = self._turn(self._router(None), request)
        self.assertEqual((result.source, result.handled), ("decomposed", True), result.text)
        self.assertEqual(sorted(q for _e, q in self.asked),
                         sorted(["remember that my editor is vim", JOKE]))


if __name__ == "__main__":
    unittest.main()
