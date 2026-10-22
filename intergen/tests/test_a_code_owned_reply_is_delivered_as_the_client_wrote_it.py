# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A reply the model client wrote is delivered as the client wrote it.

Once its retry ladder is spent the model client serves a sentence of its own in
place of a model's answer and names it on the reply (code_owned): the
server-down text when nothing listens at the model server's address, the
empty-response text when two attempts produced nothing servable. The freeform
route read that name only for its citation step, after the delivery screens had
already run on the text:

- the refusal screen read the server-down text ("I can't answer that right
  now") as a refusal of a general question and replaced it with the scope
  steer, so the person was offered the frontier model and never told the model
  server is down;
- the semantic-flag rung discarded the empty-response text when it carried the
  flags of the completion it replaced, and served the incoherence fallback;
- the first reply's name was added to the turn's code-owned names whatever the
  screens delivered, so a server-down text the refusal screen regenerated into a
  model answer lost that answer's Source line, and the turn logged the model's
  answer as the client's text.

The name is now set before anything reads the text, and no screen reads it.
Every case drives the real model client through its one request function.
"""
from __future__ import annotations

import unittest
import urllib.error
import urllib.request
from unittest import mock

from intergen.interfaces.types import HardwareTierLevel
from intergen.llm import LLMRouter
from intergen.tests.test_truth_screens import (
    OWN_ANSWER, TOOLCHAINS_PAGE, WHAT_LEAVES_INTRO_PAGE, _OneHit, _REFUSED, _router,
    _SseResponse, _sse_text, _WHAT_LEAVES_CITATION)

SERVER_DOWN = LLMRouter._MODEL_SERVER_DOWN_FALLBACK
EMPTY_RESPONSE = LLMRouter._EMPTY_RESPONSE_FALLBACK
GENERAL_ASK = "compose a poem about autumn"
MODEL_REFUSAL = "I can't help with that request."
# Rejected by the client's quality gate and flagged by its semantic screen, twice.
REPETITION = " ".join(["the"] * 20)


def _turn(user, *replies, locked, wiki=None):
    """One turn through route() with the REAL model client, the model server
    answered at the client's one request function: each request gets the next
    of `replies`, the last repeating — `_REFUSED` for a server nothing listens
    on, any other text streamed back as the completion. `wiki` is the page the
    installed wiki returns for the turn (none when not given). Returns (the
    result, the router, the number of requests, the glass emit mock)."""
    tier = HardwareTierLevel.TIER_1 if locked else HardwareTierLevel.TIER_2
    router = _router(tier=tier, locked=locked)
    del router._llm.chat, router._llm.stream_with_tools
    router._wiki_retrieval = _OneHit(wiki)
    asked = []

    def urlopen(request, *_, **__):
        asked.append(request)
        reply = replies[min(len(asked), len(replies)) - 1]
        if reply == _REFUSED:
            raise urllib.error.URLError(
                ConnectionRefusedError(111, "Connection refused"))
        return _SseResponse(_sse_text(reply))

    with mock.patch.object(urllib.request, "urlopen", side_effect=urlopen), \
            mock.patch("intergen.router.glass.emit") as emit:
        result = router.route(user)
    return result, router, len(asked), emit


def _decisions(emit, name):
    return [c.kwargs.get("detail") or c.args[2] for c in emit.call_args_list
            if c.args[:2] == ("decision", name)]


class TheClientsTextIsDeliveredAsWritten(unittest.TestCase):
    """The client's own text reaches the person as the client wrote it, on both
    floors, and no screen reads it."""

    def _assert_delivered_as_written(self, result, text, emit):
        self.assertEqual(result.source, "llm_freeform")
        self.assertEqual(result.text, text)
        self.assertEqual(_decisions(emit, "refusal_screen"), [])
        self.assertEqual(_decisions(emit, "claim_screen"), [])

    def test_the_server_down_text_reaches_the_person_on_the_locked_floor(self):
        result, _, asked, emit = _turn(GENERAL_ASK, _REFUSED, locked=True)
        self._assert_delivered_as_written(result, SERVER_DOWN, emit)
        self.assertEqual(asked, 1)
        self.assertEqual([d["name"] for d in _decisions(emit, "code_owned_delivery")],
                         ["model_server_down"])

    def test_the_server_down_text_reaches_the_person_on_the_native_floor(self):
        result, _, _, emit = _turn(GENERAL_ASK, _REFUSED, locked=False)
        self._assert_delivered_as_written(result, SERVER_DOWN, emit)
        self.assertEqual([d["name"] for d in _decisions(emit, "code_owned_delivery")],
                         ["model_server_down"])

    def test_the_empty_response_text_reaches_the_person_on_both_floors(self):
        for locked in (True, False):
            with self.subTest(locked=locked):
                result, _, _, emit = _turn(GENERAL_ASK, "", locked=locked)
                self._assert_delivered_as_written(result, EMPTY_RESPONSE, emit)

    def test_the_flags_of_a_replaced_completion_do_not_discard_the_clients_text(self):
        # The model repeats itself twice; the client serves its empty-response
        # text with the semantic screen's flags of that completion on the reply.
        result, _, asked, emit = _turn(GENERAL_ASK, REPETITION, locked=True)
        self.assertEqual(asked, 2)
        self._assert_delivered_as_written(result, EMPTY_RESPONSE, emit)
        self.assertEqual(_decisions(emit, "semantic_flags"), [])

    def test_the_name_is_set_before_any_screen_reads_the_text(self):
        seen = []
        tier = HardwareTierLevel.TIER_1
        router = _router(tier=tier, locked=True)
        del router._llm.chat, router._llm.stream_with_tools
        router._wiki_retrieval = _OneHit(None)
        screens = router._screen_model_answer

        def spy(draft, messages, **kwargs):
            seen.append(list(kwargs.get("fallbacks") or []))
            return screens(draft, messages, **kwargs)

        router._screen_model_answer = spy
        with mock.patch.object(urllib.request, "urlopen", side_effect=urllib.error.URLError(
                ConnectionRefusedError(111, "Connection refused"))), \
                mock.patch("intergen.router.glass.emit"):
            result = router.route(GENERAL_ASK)
        self.assertEqual(seen, [["model_server_down"]])
        self.assertEqual(result.text, SERVER_DOWN)


class ANameIsRecordedOnlyForTheTextDelivered(unittest.TestCase):
    """The first reply's name names the text the turn delivers, and nothing
    else: a model answer is never recorded as the client's text."""

    def test_the_server_down_text_is_what_its_name_records(self):
        # The server is down for the first request and answers the next with
        # the page's own words: the client's text is delivered, and the name
        # the turn records is that text's.
        with self.assertLogs("intergen.router", level="INFO") as logs:
            result, _, asked, _ = _turn(GENERAL_ASK, _REFUSED, OWN_ANSWER,
                                        locked=True, wiki=WHAT_LEAVES_INTRO_PAGE)
        named = [m for m in logs.output if "code-owned fallback (model_server_down)" in m]
        self.assertEqual(len(named), 1)
        self.assertEqual(result.text, SERVER_DOWN)
        self.assertEqual(asked, 1)

    def test_an_accepted_regeneration_of_a_model_reply_keeps_its_page(self):
        # The model refuses, the refusal screen asks again, and the second
        # reply is the model's answer drawn from the page: it is cited, and no
        # name is recorded for it.
        with self.assertLogs("intergen.router", level="INFO") as logs:
            result, _, asked, emit = _turn(GENERAL_ASK, MODEL_REFUSAL, OWN_ANSWER,
                                           locked=True, wiki=WHAT_LEAVES_INTRO_PAGE)
        self.assertEqual(asked, 2)
        self.assertTrue(result.text.startswith(OWN_ANSWER))
        self.assertTrue(result.text.endswith(_WHAT_LEAVES_CITATION))
        self.assertEqual([m for m in logs.output if "code-owned fallback" in m], [])
        self.assertEqual([d["verdict"] for d in _decisions(emit, "refusal_screen")],
                         ["refusal_regenerated"])

    def test_a_model_that_refuses_twice_still_meets_the_refusal_screen(self):
        result, _, asked, emit = _turn(GENERAL_ASK, MODEL_REFUSAL, locked=True,
                                       wiki=TOOLCHAINS_PAGE)
        self.assertEqual(asked, 2)
        self.assertEqual([d["verdict"] for d in _decisions(emit, "refusal_screen")],
                         ["refusal_regen_failed_steer"])
        self.assertNotIn(SERVER_DOWN, result.text)
        self.assertNotIn("Source:", result.text)


if __name__ == "__main__":
    unittest.main()
