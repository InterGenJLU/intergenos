# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Saying yes to an offered web search must run that search.

THE DEFECT, from three days of an ordinary person's use. InterGen answered a
live-data question with an honest offer — "That's live data I can't know from
memory — but I can search the web for it right now. Want me to look it up?" —
and then, when she said yes, did not search. Nothing anywhere held the question
she had asked, so the affirmative turn had nothing to consume: it was routed as
if it were a brand-new sentence with no history behind it.

What she saw, three times over three days, and what this file pins:

  * She asked when the weather would cool off in a named town. She was offered
    the search, and answered "yes, please look up the weather cooling for
    Gardendale, AL." — naming the town again, in the same breath. The reply was
    "I don't know your location". The place had been in both of her turns.
  * She asked what a whole-house generator costs, was offered the search, and
    said "please do". The reply was a from-memory answer that opened "I don't
    have access to real-time data" — the opposite of the offer she had just
    accepted.
  * She asked what time the sun would set in a named town. The reply was the
    current clock time. "what time" alone was enough for the time-of-day intent
    to claim the sentence and run `date`.

In three days exactly one web search ran, and only because she happened to
phrase a question as "search the web for ...". An offer that cannot be accepted
is worse than no offer: it teaches a person the assistant is unreliable at the
moment it has just promised to help.

WHAT IS ASSERTED HERE. That the router PRODUCES the web_search call, with the
question carried into it — measured at the dispatch seam, with the tool
registry's execute intercepted. No network request is made by these cases and
none is asserted: whether the search itself returns anything useful is the
tool's business and a live leg, not this one's.

The sentences below are quoted from the field transcripts. Nothing else from
that data is read, copied, or asserted on.
"""
from __future__ import annotations

import unittest
from unittest import mock

from intergen.interfaces.types import ToolResult
from intergen.intents import register_all_intents
from intergen.llm import LLMRouter
from intergen.router import ConversationRouter
from intergen.semantic import SemanticMatcher
from intergen.tool_registry import ToolRegistry

# Her sentences, verbatim.
WEATHER_ASK = "when is the weather supposed to cool off in Gardendale, AL?"
WEATHER_YES = "yes, please look up the weather cooling for Gardendale, AL."
GENERATOR_ASK = "how much can a whole house generator cost?"
GENERATOR_YES = "please do"
SUNSET_ASK = "What time will the sun set in Mount Olive, AL, today?"

OFFER_SOURCE = "current_data_offer"

_REG = ToolRegistry()
_REG.discover_tools()


def _router() -> ConversationRouter:
    """A router built the way the daemon builds one.

    dbus_daemon.py registers every intent on the matcher at startup, so a router
    without them is not the surface a person meets — the deterministic intent
    patterns are consulted before the model ever sees the turn. Leaving them out
    hides the time-of-day capture entirely.
    """
    matcher = SemanticMatcher(embedder=None)
    register_all_intents(matcher)
    return ConversationRouter(
        tool_registry=_REG, semantic_matcher=matcher,
        llm=LLMRouter(config=None), lock_dispatch=True)


class _DispatchRecorder:
    """Stands in for ToolRegistry.execute and remembers what it was asked to run.

    Nothing reaches the network: every call returns a canned success. What the
    cases assert is the CALL the router produced — the join between deciding to
    search and searching.
    """

    def __init__(self) -> None:
        self.calls: list = []

    def __call__(self, call, *args, **kwargs):
        self.calls.append(call)
        return ToolResult(call_id=getattr(call, "call_id", "") or "probe",
                          name=call.name,
                          content="(search results withheld in this test)",
                          success=True, executed=True)

    @property
    def names(self) -> list[str]:
        return [c.name for c in self.calls]

    def query_of(self, tool: str) -> str:
        for c in self.calls:
            if c.name == tool:
                return str(c.arguments.get("query", ""))
        return ""


class _RoutedExchange(unittest.TestCase):
    """Drives a whole exchange through one router, recording every dispatch."""

    def _exchange(self, turns: list[str]):
        recorder = _DispatchRecorder()
        router = _router()
        results = []
        with mock.patch.object(ToolRegistry, "execute", recorder):
            for turn in turns:
                results.append(router.route(turn))
        return results, recorder


class TheAffirmativeRunsTheOfferedSearch(_RoutedExchange):
    """The heart of it: yes means search."""

    def test_the_offer_is_still_made(self) -> None:
        """Control: the offer itself was never the defect and must not change."""
        results, _ = self._exchange([WEATHER_ASK])
        self.assertEqual(results[0].source, OFFER_SOURCE)
        self.assertIn("search", results[0].text.lower())

    def test_yes_naming_the_place_searches_for_that_place(self) -> None:
        results, recorder = self._exchange([WEATHER_ASK, WEATHER_YES])
        self.assertIn("web_search", recorder.names, (
            "she accepted the offer and no search was dispatched. The reply "
            f"was: {results[1].text[:160]!r}"))
        query = recorder.query_of("web_search")
        self.assertIn("Gardendale", query,
                      f"the search ran without the town she named twice: {query!r}")
        self.assertIn("weather", query.lower(),
                      f"the search ran without the subject she asked about: {query!r}")

    def test_the_reply_does_not_claim_it_has_no_location(self) -> None:
        results, _ = self._exchange([WEATHER_ASK, WEATHER_YES])
        self.assertNotIn("I don't know your location", results[1].text, (
            "she named the town in the same sentence she accepted with"))

    def test_a_bare_yes_searches_for_the_question_that_was_offered(self) -> None:
        results, recorder = self._exchange([GENERATOR_ASK, GENERATOR_YES])
        self.assertIn("web_search", recorder.names, (
            "'please do' accepted the offer and nothing was searched. The "
            f"reply was: {results[1].text[:160]!r}"))
        query = recorder.query_of("web_search")
        self.assertIn("generator", query.lower(),
                      f"the accepted question was not carried forward: {query!r}")

    def test_the_accepting_turn_is_not_handed_to_the_model(self) -> None:
        """What she actually got was the model's own answer, which opened by
        denying it could reach live data — the opposite of the offer she had
        just accepted. The exact wording is the model's and varies; what is
        measurable, and what went wrong, is that the turn reached the model at
        all instead of being consumed by the offer that was standing."""
        results, _ = self._exchange([GENERATOR_ASK, GENERATOR_YES])
        self.assertNotEqual(results[1].source, "llm_freeform", (
            "the turn accepting a web-search offer was handed to the model to "
            "answer from memory"))


class SayingNoStillMeansNo(_RoutedExchange):
    """The other half of consent. A declined offer must not search."""

    def test_a_bare_no_runs_nothing(self) -> None:
        _, recorder = self._exchange([GENERATOR_ASK, "no thanks"])
        self.assertNotIn("web_search", recorder.names,
                         "a declined offer ran the search anyway")


class AnUnrelatedFollowUpIsNotConsent(_RoutedExchange):
    """The control that keeps the fix honest: only an acceptance accepts."""

    def test_a_new_question_after_an_offer_is_not_a_search_of_the_old_one(self) -> None:
        _, recorder = self._exchange(
            [GENERATOR_ASK, "actually, what is the capital of France?"])
        self.assertEqual(
            recorder.query_of("web_search"), "",
            "an unrelated next question was consumed as consent to the "
            "previous offer")


class TheSunsetQuestionIsNotAClockQuestion(_RoutedExchange):
    """"What time will the sun set" is a daylight question about a place."""

    def test_the_answer_is_not_the_current_time(self) -> None:
        results, _ = self._exchange([SUNSET_ASK])
        text = results[0].text or ""
        self.assertNotIn("It's currently", text, (
            "a question about sunset in a named town was answered with this "
            f"machine's clock: {text[:160]!r}"))

    def test_it_is_met_as_live_data_it_cannot_know(self) -> None:
        results, _ = self._exchange([SUNSET_ASK])
        self.assertEqual(results[0].source, OFFER_SOURCE, (
            "the sunset question should be met the way every other live-data "
            f"question is — with an offer to look it up. Got source="
            f"{results[0].source!r}, text={ (results[0].text or '')[:160]!r}"))


class TheClockStillAnswersClockQuestions(_RoutedExchange):
    """Control. The time-of-day intent exists because "what time is it" was
    falling to a fifty-second tool-selection detour; that must keep working."""

    def test_what_time_is_it_still_reads_the_clock(self) -> None:
        results, _ = self._exchange(["what time is it?"])
        self.assertIn("currently", (results[0].text or "").lower(), (
            "the plain clock question stopped being answered from the clock: "
            f"{(results[0].text or '')[:160]!r}"))

    def test_the_date_question_still_reads_the_date(self) -> None:
        results, _ = self._exchange(["what is today's date?"])
        self.assertTrue((results[0].text or "").strip(),
                        "the date question stopped being answered")


if __name__ == "__main__":
    unittest.main()
