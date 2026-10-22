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
A regeneration a screen asks for that comes back as the client's text is
delivered the same way, and the router's callers of chat() are enumerated,
so a new one is classified before it can serve the client's text unnamed.
Every route() case drives the real model client through its one request
function.
"""
from __future__ import annotations

import ast
import textwrap
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from intergen import router as router_module
from intergen.interfaces.provenance import Provenance
from intergen.interfaces.types import HardwareTierLevel, LLMResponse, ToolCall, ToolResult
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


SAVE_ASK = "compose a poem, then save it"


class NoSaveIsStagedFromCodesText(unittest.TestCase):
    """A text code wrote is no artifact of the person's: asked to compose and
    save, the turn stages no save of the client's text or of a screen's (the
    scope steer was staged as draft.txt when the server was down)."""

    def test_no_save_of_the_server_down_text_on_either_floor(self):
        for locked in (True, False):
            with self.subTest(locked=locked):
                result, router, _, _ = _turn(SAVE_ASK, _REFUSED, locked=locked)
                self.assertEqual(result.text, SERVER_DOWN)
                self.assertIsNone(router._conv.pending_action_offer)

    def test_no_save_of_a_screens_text(self):
        # The model refuses twice, so the refusal screen serves the scope steer.
        result, router, asked, _ = _turn(SAVE_ASK, MODEL_REFUSAL, locked=True)
        self.assertEqual(asked, 2)
        self.assertIn("frontier model", result.text)
        self.assertIsNone(router._conv.pending_action_offer)

    def test_the_models_own_poem_is_still_offered_for_saving(self):
        poem = ("Amber leaves descend in quiet spirals,\nthe orchard hums a slower "
                "song,\nand every lamp along the lane\nkeeps the evening company "
                "all night long.")
        result, router, _, _ = _turn(SAVE_ASK, poem, locked=True)
        self.assertTrue(result.text.startswith(poem))
        offer = router._conv.pending_action_offer
        self.assertIsNotNone(offer)
        self.assertEqual(offer[1], "write_file")
        self.assertEqual(offer[3].get("content"), poem)


class TheLinkageNamesWhoWroteTheText(unittest.TestCase):
    """The freeform route's answer linkage reads kind "code" with the writer's
    name whenever the delivered text is code's, and kind "model" otherwise."""

    def test_the_clients_text_is_linked_to_the_client(self):
        for locked in (True, False):
            with self.subTest(locked=locked):
                result, _, _, _ = _turn(GENERAL_ASK, _REFUSED, locked=locked)
                self.assertEqual((result.answer_linkage.kind,
                                  result.answer_linkage.renderer),
                                 ("code", "model_server_down"))

    def test_a_screens_text_is_linked_to_the_screen(self):
        result, _, _, _ = _turn(GENERAL_ASK, MODEL_REFUSAL, locked=True)
        self.assertEqual((result.answer_linkage.kind, result.answer_linkage.renderer),
                         ("code", "refusal_screen"))

    def test_the_models_answer_is_linked_to_the_model(self):
        result, _, _, _ = _turn(GENERAL_ASK, OWN_ANSWER, locked=True,
                                wiki=WHAT_LEAVES_INTRO_PAGE)
        self.assertTrue(result.text.endswith(_WHAT_LEAVES_CITATION))
        self.assertEqual((result.answer_linkage.kind, result.answer_linkage.renderer),
                         ("model", "llm_freeform"))


class ARegenerationIntoTheClientsTextIsDeliveredAsWritten(unittest.TestCase):
    """The second door: a draft one screen flags is asked for again, and the
    regeneration comes back as the client's own text because the model server
    went down between the two requests. Each screen accepts it as the client
    wrote it, and no later screen reads it (the refusal screen read the
    server-down text as a refusal and served the scope steer)."""

    DRAFTS = {
        "state_claim_screen": "No problem, your disk is 80% full.",
        "model_offer_screen": "I can install htop for you if you'd like — just say yes.",
        "capability_screen": "Run `pkm frobnicate htop` to get it.",
        "refusal_screen": MODEL_REFUSAL,
    }
    REGENERATED = {"state_claim_screen": "violation_regenerated",
                   "model_offer_screen": "violation_regenerated",
                   "capability_screen": "violation_regenerated",
                   "refusal_screen": "refusal_regenerated"}

    def test_each_screen_delivers_the_server_down_text_as_written(self):
        for screen, draft in self.DRAFTS.items():
            with self.subTest(screen=screen):
                result, router, asked, emit = _turn(
                    GENERAL_ASK, draft, _REFUSED, locked=True, wiki=TOOLCHAINS_PAGE)
                self.assertEqual(asked, 2)
                self.assertEqual(result.text, SERVER_DOWN)
                self.assertEqual([d["verdict"] for d in _decisions(emit, screen)],
                                 [self.REGENERATED[screen]])
                self.assertEqual((result.answer_linkage.kind,
                                  result.answer_linkage.renderer),
                                 ("code", "model_server_down"))
                self.assertIsNone(router._conv.pending_action_offer)


# What the stood-in command printed for the dispatched turn below.
DISK_USAGE = ("Filesystem      Size  Used Avail Use% Mounted on\n"
              "/dev/nvme0n1p2  100G   20G   80G  20% /")


def _dispatched_turn(user, draft):
    """One turn on the native floor's tool route in which the model calls a
    tool before it answers: the call is dispatched (the registry stood in, the
    command ran and printed DISK_USAGE), the answer composed after it is
    `draft`, and every request after that — a screen's regeneration, through
    the REAL model client's chat() — finds the model server down. Returns (the
    result, the router, the number of requests, the glass emit mock)."""
    router = _router(tier=HardwareTierLevel.TIER_2, locked=False)
    del router._llm.chat
    router._wiki_retrieval = _OneHit(TOOLCHAINS_PAGE)
    call = ToolCall(name="run_command", arguments={"command": "df -h"},
                    call_id="c1", source_of_request=Provenance.USER_DIRECT)
    router._llm.stream_with_tools = mock.Mock(return_value=iter([call]))
    router._tools.execute = mock.Mock(return_value=ToolResult(
        call_id="c1", name="run_command", content=DISK_USAGE))
    router._llm.continue_after_tool_call = mock.Mock(return_value=LLMResponse(
        text=draft, model="local", local=True, quality_passed=True))
    asked = []

    def urlopen(request, *_, **__):
        asked.append(request)
        raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))

    with mock.patch.object(urllib.request, "urlopen", side_effect=urlopen), \
            mock.patch("intergen.router.glass.emit") as emit:
        result = router.route(user)
    return result, router, len(asked), emit


class TheToolRouteDeliversTheClientsTextAsWritten(unittest.TestCase):
    """The second door on the native floor's tool route, where every
    conversational turn is offered the tools. A screen flags the model's answer
    and asks again, and the regeneration comes back as the client's own text.
    It is delivered as the freeform route delivers it: linked to the client,
    one code_owned_delivery row, no Source line, no save, and no later screen
    reads it. Before, the route passed the screens no list: the text was linked
    to the model or to the dispatch, no row was written, and the refusal screen
    read the server-down text as a refusal and asked the model a third time."""

    DRAFTS = ARegenerationIntoTheClientsTextIsDeliveredAsWritten.DRAFTS
    REGENERATED = ARegenerationIntoTheClientsTextIsDeliveredAsWritten.REGENERATED

    def _assert_delivered_as_written(self, screen, result, router, emit):
        self.assertEqual(result.source, "llm_tools")
        self.assertEqual(result.text, SERVER_DOWN)
        self.assertEqual([d["verdict"] for d in _decisions(emit, screen)],
                         [self.REGENERATED[screen]])
        if screen != "refusal_screen":
            self.assertEqual(_decisions(emit, "refusal_screen"), [])
        self.assertEqual((result.answer_linkage.kind,
                          result.answer_linkage.renderer),
                         ("code", "model_server_down"))
        self.assertEqual([d["name"] for d in _decisions(emit, "code_owned_delivery")],
                         ["model_server_down"])
        self.assertNotIn("Source:", result.text)
        self.assertIsNone(router._conv.pending_action_offer)

    def test_the_text_answer_delivers_the_server_down_text_as_written(self):
        # The model answers in text without calling a tool (the first request,
        # streamed back by the client), and the screen's regeneration is the
        # second.
        for screen, draft in self.DRAFTS.items():
            with self.subTest(screen=screen):
                result, router, asked, emit = _turn(
                    GENERAL_ASK, draft, _REFUSED, locked=False, wiki=TOOLCHAINS_PAGE)
                self._assert_delivered_as_written(screen, result, router, emit)
                self.assertEqual(asked, 2)

    def test_the_answer_after_a_dispatch_delivers_the_server_down_text_as_written(self):
        # The stream and the synthesis are stood in, so the screen's
        # regeneration is the only request the turn makes. The state-claim and
        # offer screens pass an answer a dispatch stands behind
        # (safety.screen_local_state_claim, screen_model_text_offer), so after
        # a dispatch the capability and refusal screens are the ones that ask
        # again.
        for screen in ("capability_screen", "refusal_screen"):
            draft = self.DRAFTS[screen]
            with self.subTest(screen=screen):
                result, router, asked, emit = _dispatched_turn(GENERAL_ASK, draft)
                self._assert_delivered_as_written(screen, result, router, emit)
                self.assertEqual(asked, 1)
                self.assertEqual([r.name for r in result.tool_results], ["run_command"])

    def test_the_models_answer_after_a_dispatch_is_still_linked_to_the_dispatch(self):
        """CONTROL: an answer no screen replaced keeps the dispatch's linkage."""
        poem = "Amber leaves descend in quiet spirals over the orchard."
        result, _, asked, emit = _dispatched_turn(GENERAL_ASK, poem)
        self.assertEqual(asked, 0)
        self.assertEqual(result.text, poem)
        self.assertEqual((result.answer_linkage.kind, result.answer_linkage.tool,
                          result.answer_linkage.call_id, result.answer_linkage.renderer),
                         ("dispatch", "run_command", "c1", "llm_synth"))
        self.assertEqual(_decisions(emit, "code_owned_delivery"), [])


class ACallThePersonDeniedIsToldInTheCodesWords(unittest.TestCase):
    """The native floor's tool route: the model calls a state-changing tool, the
    call is held for the person's consent, and the person denies it at the card.
    The denial is told as the keyword path and the web panel tell it, by the one
    renderer every surface uses (gate_refusal_message): the model is never asked
    to narrate it (it narrated the denial as it chose to word it)."""

    NARRATION = "I tried to install htop, but the safety layer blocked it."

    def _denied_turn(self):
        router = _router(tier=HardwareTierLevel.TIER_2, locked=False)
        del router._llm.chat
        call = ToolCall(name="manage_packages",
                        arguments={"action": "install", "package": "htop"},
                        call_id="c2", source_of_request=Provenance.USER_DIRECT)
        denied = ToolResult(call_id="c2", name="manage_packages",
                            content="Tool call denied by user via review modal.",
                            success=False, executed=False, denied_by_user=True)
        router._llm.stream_with_tools = mock.Mock(return_value=iter([call]))
        router._tools.execute = mock.Mock(return_value=denied)
        router._llm.continue_after_tool_call = mock.Mock(return_value=LLMResponse(
            text=self.NARRATION, model="local", local=True, quality_passed=True))
        asked = []

        def urlopen(request, *_, **__):
            asked.append(request)
            raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))

        with mock.patch.object(urllib.request, "urlopen", side_effect=urlopen), \
                mock.patch("intergen.router.glass.emit"):
            result = router.route(self.ASK)
        return result, router, call, denied, len(asked)

    # No keyword or curated route claims it, so the native floor offers the
    # model its tools; the model's call is the stood-in stream's.
    ASK = "I keep wishing I had a nicer process viewer than top"

    def test_the_denial_is_rendered_by_the_code_and_never_narrated(self):
        from intergen.tool_registry import gate_refusal_message
        result, router, call, denied, asked = self._denied_turn()
        self.assertEqual(result.source, "llm_tools")
        self.assertEqual(result.text, gate_refusal_message(
            call, denied, router._tools.get_tool("manage_packages")))
        self.assertNotIn(self.NARRATION, result.text)
        router._llm.continue_after_tool_call.assert_not_called()
        self.assertEqual(asked, 0)
        self.assertEqual((result.answer_linkage.kind, result.answer_linkage.tool,
                          result.answer_linkage.call_id, result.answer_linkage.renderer),
                         ("code", "manage_packages", "c2", "gate_refusal"))
        self.assertEqual([r.name for r in result.tool_results], ["manage_packages"])


class ACallThatRanNothingIsToldAsThePanelTellsIt(unittest.TestCase):
    """The native floor's tool route through the real registry: the model calls
    a state-changing tool, the registry holds it for the person's review, and
    the review answers anything but an approval. Every such call ran nothing,
    and the route tells it as the web panel does, by the panel's condition (not
    executed, not succeeded) and its one renderer, gate_refusal_message, one
    case per review outcome: the model is never asked to narrate it. Before,
    only the person's own denial was told that way, and a card that timed out
    or a call with no review surface reached the synthesis."""

    ASK = ACallThePersonDeniedIsToldInTheCodesWords.ASK
    NARRATION = ACallThePersonDeniedIsToldInTheCodesWords.NARRATION

    def _turn(self, review_callback):
        from intergen import tool_registry as registry_module
        router = _router(tier=HardwareTierLevel.TIER_2, locked=False)
        del router._llm.chat
        dispatched = []

        def dispatch(call, *a, **k):
            dispatched.append(call.arguments)
            return ToolResult(call_id=call.call_id, name=call.name,
                              content="ran", success=True)
        call = ToolCall(name="manage_packages",
                        arguments={"action": "install", "package": "htop"},
                        call_id="c3", source_of_request=Provenance.USER_DIRECT)
        router._llm.stream_with_tools = mock.Mock(return_value=iter([call]))
        router._llm.continue_after_tool_call = mock.Mock(return_value=LLMResponse(
            text=self.NARRATION, model="local", local=True, quality_passed=True))
        reviewed = []

        def review(call, decision):
            reviewed.append(decision.action)
            return review_callback(call, decision)
        asked = []

        def urlopen(request, *_, **__):
            asked.append(request)
            raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))

        with mock.patch.object(urllib.request, "urlopen", side_effect=urlopen), \
                mock.patch("intergen.router.glass.emit"), \
                mock.patch.object(registry_module, "write_record", return_value=True), \
                mock.patch.object(registry_module, "mint_token", return_value="case-token"), \
                mock.patch.object(router._tools, "_dispatch_via_pkexec",
                                  side_effect=dispatch):
            # The callback is the turn's: route() takes it as the daemon and
            # the panel hand it in, and a route() without one has no review
            # surface.
            result = router.route(
                self.ASK,
                review_callback=None if review_callback is None else review)
        return result, router, call, dispatched, len(asked), reviewed

    def assert_told_by_the_code(self, review_callback):
        from intergen.tool_registry import gate_refusal_message
        result, router, call, dispatched, asked, reviewed = self._turn(
            review_callback)
        # The call was held for the person's review and the review was asked,
        # or there was no review surface to ask.
        self.assertEqual(reviewed, [] if review_callback is None else ["execute"])
        self.assertEqual(dispatched, [], "the held call ran")
        self.assertEqual([r.name for r in result.tool_results], ["manage_packages"])
        ran = result.tool_results[0]
        self.assertFalse(ran.executed)
        self.assertFalse(ran.success)
        self.assertEqual(result.source, "llm_tools")
        self.assertEqual(result.text, gate_refusal_message(
            call, ran, router._tools.get_tool("manage_packages")))
        self.assertNotIn(self.NARRATION, result.text)
        router._llm.continue_after_tool_call.assert_not_called()
        self.assertEqual(asked, 0)
        self.assertEqual((result.answer_linkage.kind, result.answer_linkage.tool,
                          result.answer_linkage.call_id, result.answer_linkage.renderer),
                         ("code", "manage_packages", "c3", "gate_refusal"))
        return ran

    def test_each_review_answer_that_is_not_an_approval(self):
        from intergen.tool_registry import (
            REFUSED_WITHOUT_REVIEW, REVIEW_ENDED, REVIEW_FAILED, REVIEW_REFUSED,
            REVIEW_TIMED_OUT)
        for answer in ("deny", "deny_conversation", REVIEW_TIMED_OUT,
                       REVIEW_REFUSED, REVIEW_FAILED, REFUSED_WITHOUT_REVIEW,
                       REVIEW_ENDED, "an answer no review surface gives"):
            with self.subTest(answer=answer):
                ran = self.assert_told_by_the_code(lambda c, d, a=answer: a)
                self.assertEqual(ran.denied_by_user,
                                 answer in ("deny", "deny_conversation"))

    def test_a_review_surface_that_raised(self):
        def raises(call, decision):
            raise RuntimeError("the card could not be drawn")
        self.assertFalse(self.assert_told_by_the_code(raises).denied_by_user)

    def test_no_review_surface(self):
        self.assertFalse(self.assert_told_by_the_code(None).denied_by_user)


# Every function of the router that asks the model client for a reply, with
# what it does with a reply the client wrote.
CHAT_CALLERS = {
    "_try_llm_freeform": "the first reply: its name is set before anything "
                         "reads it, and it is delivered as the client wrote it",
    "_regenerate_without_claim": "a screen's regeneration: returned only "
                                 "through _accepted_regeneration",
    "_regenerate_without_state_claim": "a screen's regeneration: returned only "
                                       "through _accepted_regeneration",
    "_regenerate_without_selfoffer": "a screen's regeneration: returned only "
                                     "through _accepted_regeneration",
    "_regenerate_with_capability_grounding": "a screen's regeneration: "
                                             "returned only through "
                                             "_accepted_regeneration",
    "_regenerate_without_refusal": "a screen's regeneration: returned only "
                                   "through _accepted_regeneration",
    "_try_system_map": "the system map: its synthesis gate refuses a reply "
                       "whose ladder was spent, and the live data is served",
    "_synthesize_tool_result": "the tool route's synthesis: the same gate, and "
                               "the tool's own output is served",
}


def _is_chat_call(node):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "chat"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "_llm")


def _chat_callers(source):
    """The functions that call the model client's chat(), by name."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(_is_chat_call(sub) for sub in ast.walk(node)):
                found.add(node.name)
    return found


def _returned(expr):
    """The expressions a return can hand back: both branches of a conditional
    expression, never its condition (which only reads the reply)."""
    if isinstance(expr, ast.IfExp):
        return _returned(expr.body) + _returned(expr.orelse)
    return [expr]


def _regeneration_returns(source):
    """Each return of a _regenerate_* function that can hand back a chat()
    reply (or anything read from it) other than as an argument of
    self._accepted_regeneration: [(function, line)]."""
    problems = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name.startswith("_regenerate_")):
            continue
        replies = {target.id for sub in ast.walk(node) if isinstance(sub, ast.Assign)
                   and _is_chat_call(sub.value)
                   for target in sub.targets if isinstance(target, ast.Name)}
        for ret in (sub for sub in ast.walk(node) if isinstance(sub, ast.Return)):
            if ret.value is None:
                continue
            for value in _returned(ret.value):
                allowed = {id(arg) for call in ast.walk(value)
                           if isinstance(call, ast.Call)
                           and isinstance(call.func, ast.Attribute)
                           and call.func.attr == "_accepted_regeneration"
                           for arg in call.args if isinstance(arg, ast.Name)}
                if any(isinstance(sub, ast.Name) and sub.id in replies
                       and id(sub) not in allowed for sub in ast.walk(value)):
                    problems.append((node.name, ret.lineno))
    return problems


class EveryReplyOfTheClientIsAccountedFor(unittest.TestCase):
    """The router's chat() callers are the classified eight, and a screen's
    regeneration reaches the turn only through _accepted_regeneration, the one
    place a regeneration's code-owned name is read."""

    SOURCE = Path(router_module.__file__).read_text(encoding="utf-8")

    def test_the_callers_of_chat_are_the_classified_eight(self):
        self.assertEqual(_chat_callers(self.SOURCE), set(CHAT_CALLERS))

    def test_no_regeneration_returns_a_replys_text_but_through_the_one_reader(self):
        self.assertEqual(_regeneration_returns(self.SOURCE), [])

    def test_the_reading_finds_a_regeneration_that_returns_the_text_itself(self):
        # The control: a helper that returns the reply's text directly is found.
        source = textwrap.dedent("""
            class Router:
                def _regenerate_direct(self, messages, *, fallbacks=None):
                    regen = self._llm.chat(messages)
                    return regen.text if regen.text else None

                def _regenerate_checked(self, messages, *, fallbacks=None):
                    regen = self._llm.chat(messages)
                    return (self._accepted_regeneration(regen, fallbacks)
                            if regen.text else None)

                def _regenerate_accepted(self, messages, *, fallbacks=None):
                    regen = self._llm.chat(messages)
                    return self._accepted_regeneration(regen, fallbacks)
            """)
        self.assertEqual([name for name, _ in _regeneration_returns(source)],
                         ["_regenerate_direct"])
        self.assertEqual(_chat_callers(source),
                         {"_regenerate_direct", "_regenerate_checked",
                          "_regenerate_accepted"})


if __name__ == "__main__":
    unittest.main()
