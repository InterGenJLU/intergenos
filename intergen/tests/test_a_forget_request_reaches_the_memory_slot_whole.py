# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A request that carries a forget reaches the memory slot whole.

THE DEFECT, read on the tree in intergen/router.py. The decomposition rung
splits a request the decomposer reads as two asks, and each clause is routed by
``_route_single``, which has no memory rung. So "forget my car and tell me a
joke" sent "forget my car" to the model as a turn of its own: nothing was
forgotten, two model calls were made, and the person was answered as if the
forget had been heard. "clear all my memories and start over" did the same, and
its second clause was proposed as a service start. Two routes ahead of the rung
answer a message without asking whether it is one request: "forget my car and
what's my ip" was answered by the IP handler, which ran its address lookups, and
on the locked floor "forget my car and what's the weather right now" by the
web-search offer; the forget was dropped without a word in both.

THE RULE these cells hold the router to: a request the decomposer would split
that carries a forget verb or the reset phrase (the forget parser's verdict on
the whole request is not None) is never split, and no part of it is answered on
its own. The memory slot answers it with the parser's own declined reply, so
nothing is forgotten or cleared on a reading of part of the request, and the
person is told to say the forget on its own. With no memory store it is still
not split, and no route ahead of the rung answers part of it (the IP handler,
the web-search offer, a curated how-to): it goes to the model whole.

THE CONTROLS beside the cells are green at both ends: a two-part request with no
forget still decomposes; a request the decomposer reads as ONE request reaches
the memory slot whole, in the other order and with two subjects, and the slot
carries out the parser's verdict on the whole request, whatever that verdict
is; a forget on its own still forgets; a pure-knowledge compound still goes to
the model whole.

Every case builds the router the way the daemon builds it, on its own
throwaway store. The model is stubbed at both entry points and counted, and
every way a turn can leave the process (a child process, a socket, urllib) is
recorded and refused for the turn, so a cell measures what the router decided
with nothing on this machine or beyond it touched.
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

NOTHING_DONE = "I haven't forgotten or cleared anything:"
STORED = (("car", "a red hatchback"), ("editor", "vim"))


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


class _Case(unittest.TestCase):
    """The router, the store and one turn, as every case below uses them."""

    def _store(self) -> MemoryManager:
        """A store of its own, holding the car and the editor."""
        tmp = tempfile.TemporaryDirectory(prefix="forget-whole-")
        self.addCleanup(tmp.cleanup)
        mm = MemoryManager(os.path.join(tmp.name, "memory.db"))
        for key, value in STORED:
            mm.store(key, value)
        self.assertEqual(
            self._live_keys(mm), ["car", "editor"],
            "control: the facts were not both stored, so anything below would "
            "measure the turn against the wrong store")
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

    def assertDeclinedWhole(self, request: str,
                            tier: HardwareTierLevel = HardwareTierLevel.TIER_2) -> None:
        """The cell's outcome: the memory slot's declined reply, nothing taken,
        no model call, no tool, nothing run or reached."""
        decomposition = analyze_query(request, tier)
        self.assertTrue(
            decomposition.needs_decomposition,
            "control: the decomposer no longer reads %r as two asks, so this "
            "cell would measure a request the rung never splits" % request)
        self.assertIsNotNone(
            MemoryManager.is_forget_request(request),
            "control: the forget parser finds no forget in %r" % request)
        mm = self._store()
        result = self._turn(self._router(mm, tier), request)
        self.assertEqual(
            (result.source, result.handled), ("memory", True),
            "%r was answered by %r, not by the memory slot: %r"
            % (request, result.source, result.text))
        self.assertTrue(
            result.text.startswith(NOTHING_DONE),
            "%r was not answered with the declined reply: %r" % (request, result.text))
        self.assertEqual(self._live_keys(mm), ["car", "editor"],
                         "%r took a fact on a reading of part of it" % request)
        self.assertEqual(self.model_calls, [],
                         "%r reached the model" % request)
        self.assertEqual(list(result.tool_calls), [],
                         "%r proposed a tool" % request)
        self.assertEqual((self.processes, self.connections, self.urls), ([], [], []),
                         "%r ran or reached something" % request)


# ── the cells: red before the correction ────────────────────────────────────

class ACompoundThatCarriesAForgetIsDeclinedWhole(_Case):
    """A request the decomposer would split that carries a forget is never split."""

    def test_forget_my_car_and_tell_me_a_joke(self) -> None:
        self.assertDeclinedWhole("forget my car and tell me a joke")

    def test_forget_my_car_then_tell_me_a_joke(self) -> None:
        self.assertDeclinedWhole("forget my car, then tell me a joke")

    def test_clear_all_my_memories_and_start_over(self) -> None:
        """The reset phrase in a compound: nothing cleared, and "start over" is
        not proposed as a service start."""
        self.assertDeclinedWhole("clear all my memories and start over")

    def test_forget_about_my_car_and_what_time_is_it(self) -> None:
        self.assertDeclinedWhole("forget about my car and what time is it")

    def test_forget_my_car_and_how_do_i_update_my_system(self) -> None:
        """Held whole by the explain prior at the rung, and then read by the
        memory slot as a forget of "my car and how do i update my system"."""
        self.assertDeclinedWhole("forget my car and how do i update my system")


class NoRouteAheadOfTheRungAnswersPartOfIt(_Case):
    """The routes between the decomposer's verdict and the rung do not answer
    one clause of a request that carries a forget."""

    def test_forget_my_car_and_whats_my_ip(self) -> None:
        """Not the IP handler: its address lookups are not run."""
        self.assertDeclinedWhole("forget my car and what's my ip")

    def test_on_the_locked_floor_forget_my_car_and_the_weather(self) -> None:
        """Not the web-search offer on the locked floor."""
        self.assertDeclinedWhole("forget my car and what's the weather right now",
                                 HardwareTierLevel.TIER_1)


class WithNoMemoryStoreItIsStillNotSplit(_Case):
    """No slot to hand it to: it goes to the model whole, in one call."""

    def test_forget_my_car_and_tell_me_a_joke_goes_to_the_model_whole(self) -> None:
        request = "forget my car and tell me a joke"
        result = self._turn(self._router(None), request)
        self.assertNotEqual(result.source, "decomposed",
                            "%r was split with no memory store" % request)
        self.assertEqual(len(self.model_calls), 1,
                         "%r was not answered by the model whole: %r"
                         % (request, self.model_calls))
        self.assertEqual((self.processes, self.connections, self.urls), ([], [], []))

    def assertGoesToTheModelWhole(self, request: str,
                                  tier: HardwareTierLevel = HardwareTierLevel.TIER_2) -> None:
        """With no store, no route ahead of the rung answers one clause: the
        request reaches the model whole, in one call, nothing run or reached."""
        self.assertTrue(
            analyze_query(request, tier).needs_decomposition,
            "control: the decomposer no longer reads %r as two asks" % request)
        self.assertIsNotNone(
            MemoryManager.is_forget_request(request),
            "control: the forget parser finds no forget in %r" % request)
        result = self._turn(self._router(None, tier), request)
        self.assertNotIn(result.source, ("decomposed", "ip", "current_data_offer", "explain"),
                         "%r was answered by %r with no memory store: %r"
                         % (request, result.source, result.text))
        self.assertEqual(len(self.model_calls), 1,
                         "%r was not answered by the model whole: %r"
                         % (request, self.model_calls))
        self.assertEqual((self.processes, self.connections, self.urls), ([], [], []),
                         "%r ran or reached something" % request)

    def test_forget_my_car_and_whats_my_ip_goes_to_the_model_whole(self) -> None:
        """Not the IP handler: its address lookups are not run."""
        self.assertGoesToTheModelWhole("forget my car and what's my ip")

    def test_on_the_locked_floor_forget_my_car_and_the_weather_goes_to_the_model_whole(self) -> None:
        """Not the web-search offer on the locked floor."""
        self.assertGoesToTheModelWhole("forget my car and what's the weather right now",
                                       HardwareTierLevel.TIER_1)

    def test_forget_my_car_and_how_do_i_check_disk_space_goes_to_the_model_whole(self) -> None:
        """Not the explain route's curated how-to."""
        self.assertGoesToTheModelWhole("forget my car and how do i check disk space")


# ── the controls: green at both ends ────────────────────────────────────────

class ARequestWithNoForgetDecomposesAsBefore(_Case):

    def test_what_time_is_it_and_tell_me_a_joke_is_split(self) -> None:
        request = "what time is it and tell me a joke"
        self.assertIsNone(MemoryManager.is_forget_request(request))
        self.assertTrue(analyze_query(request, HardwareTierLevel.TIER_2).needs_decomposition)
        mm = self._store()
        result = self._turn(self._router(mm), request)
        self.assertEqual(result.source, "decomposed")
        self.assertEqual(self._live_keys(mm), ["car", "editor"])

    def test_a_pure_knowledge_compound_goes_to_the_model_whole(self) -> None:
        request = "who wrote hamlet and what year was linux created"
        self.assertIsNone(MemoryManager.is_forget_request(request))
        mm = self._store()
        result = self._turn(self._router(mm), request)
        self.assertNotEqual(result.source, "decomposed")
        self.assertEqual(len(self.model_calls), 1)


class ARequestReadAsOneReachesTheMemorySlotWhole(_Case):
    """The decomposer reads these as one request: they reach the memory slot
    whole, and the slot carries out the forget parser's verdict on the WHOLE
    request, whatever that verdict is (the parser's rules are its own)."""

    def assertSlotCarriesOutTheWholeVerdict(self, request: str) -> None:
        self.assertFalse(
            analyze_query(request, HardwareTierLevel.TIER_2).needs_decomposition,
            "control: the decomposer now reads %r as two asks" % request)
        verdict = MemoryManager.is_forget_request(request)
        self.assertIsNotNone(verdict)
        oracle = self._store()
        want_text = oracle.format_forget_response(verdict)
        want_left = self._live_keys(oracle)
        mm = self._store()
        result = self._turn(self._router(mm), request)
        self.assertEqual((result.source, result.text), ("memory", want_text))
        self.assertEqual(self._live_keys(mm), want_left)
        self.assertEqual(self.model_calls, [])

    def test_tell_me_a_joke_and_forget_my_car(self) -> None:
        self.assertSlotCarriesOutTheWholeVerdict("tell me a joke and forget my car")

    def test_forget_my_car_and_my_editor(self) -> None:
        self.assertSlotCarriesOutTheWholeVerdict("forget my car and my editor")


class AForgetOnItsOwnStillForgets(_Case):

    def test_forget_my_car(self) -> None:
        mm = self._store()
        result = self._turn(self._router(mm), "forget my car")
        self.assertEqual((result.source, result.text),
                         ("memory", "Done. I've forgotten 1 thing about 'my car'."))
        self.assertEqual(self._live_keys(mm), ["editor"])
        self.assertEqual(self.model_calls, [])


if __name__ == "__main__":
    unittest.main()
