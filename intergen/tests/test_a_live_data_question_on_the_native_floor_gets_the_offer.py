# SPDX-License-Identifier: GPL-3.0-or-later
"""A live-data question gets the code-owned offer before the model is asked, on
every floor, and a question the machine answers itself does not.

THE SHAPES THE MACHINE ANSWERS. The live-data offer (router.py
_try_current_data_offer) recognises its class by subject words and implicit live
forms, and over the demand corpora it also offered a search on questions this
machine answers itself: unit conversions, a sum over a price the person stated,
two how-tos the explain route teaches, a summary of text the person supplied, a
result from 1998, and four questions about this machine "right now". An offer
there is a deflection. Each shape fails out of the class now, on every floor, and
the live-data question next to it keeps its offer.

THE NATIVE FLOOR. A live-data question the model does not search on ("what is
the current price of bitcoin?") was answered on the native floor with a figure
the model made up, presented as current, on a turn with no tool call and so no
audit row: the dispatcher's gate on an unasked search cannot act on a turn where
the model asks for nothing. The locked floors meet that question with the offer
ahead of the model; on the native floor the offer returned at once, by a guard
that kept the model's own web decision untouched while the offer was written for
the locked floor. That decision no longer runs a search on its own (a search the
person did not ask for is held and offered), so the guard kept nothing but the
invented figure. On the native floor the question now gets the offer and the
model is never asked; "yes" runs the search through the accepted-offer resolver,
which writes the person's word for it; a question outside the class and an
explicit ask route as before; and the locked floor's answers are unchanged.
"""

from __future__ import annotations

import unittest

from intergen.tests.test_a_search_the_person_did_not_ask_for_is_offered_not_run import (
    LOCKED_OFFER, _model_search, _Recorded, _router)

#: The floors that make the offer: the locked floor (True) and the native (False).
FLOORS_THAT_OFFER = (True, False)

#: A live-data question of the class the offer recognises.
LIVE = "what is the current price of bitcoin?"
#: The figure a model invents for it, presented as current.
INVENTED = "The price of Bitcoin is currently $37,935.50."
#: A question outside the class, which the model answers itself.
NOT_LIVE = "who wrote pride and prejudice?"
#: Explicit asks, which run the search with no offer: the literal phrasing the
#: offer fails out on, and a word order only the recogniser declines it for.
EXPLICIT_ASKS = (("search the web for the price of bitcoin", "the price of bitcoin"),
                 ("do a web search for the price of bitcoin", "the price of bitcoin"))

#: Each shape: the sentences the machine answers itself (the measured ones first),
#: then the live-data questions beside them, which keep the offer.
SHAPES = {
    "a unit conversion": (
        ("convert 10 km to miles", "convert 5 miles to kilometers",
         "convert 5 miles to km", "convert 90 minutes into hours and minutes",
         "convert 100 pounds to kilograms"),
        ("convert 100 usd to euros", "convert 50 dollars to euros",
         "how much is 5000 yen in usd", "convert 100 pounds to dollars"),
    ),
    "a how-to": (
        ("how do I install nvidia drivers?",
         "if you don't mind, how do I switch between integrated and nvidia "
         "graphics?"),
        ("whats nvidia trading at",),
    ),
    "a task over supplied text": (
        ("summarize this in one sentence: The city council voted on Tuesday to "
         "approve a new zoning ordinance that will allow mixed-use development "
         "along the waterfront. Supporters argue it will bring jobs and housing, "
         "while critics worry about traffic and the loss of green space. The "
         "measure passed 5 to 2 after nearly three hours of public comment.",),
        ("how's traffic on the 405 right now", "summarize today's headlines"),
    ),
    "a sum over a stated price": (
        ("my car gets 30 mpg, gas is 3.50 a gallon, how much for a 240 mile trip",),
        ("how much is a gallon of gas right now", "nearest gas station"),
    ),
    "a machine-state question": (
        ("my laptop is SO slow right now, what's going on",
         "i free up space and its full again the next day, whats going on",
         "can you see whats on my screen right now",
         "how much memory is free right now"),
        ("coffee shops open right now", "whats the temp right now",
         "who delivers thai food to my area"),
    ),
    "a past-dated result": (
        ("who won the world cup in 1998?", "who won the world cup in 2022?"),
        ("who won the super bowl this year", "when's the next world cup",
         "who won the world cup in 2024?",
         "how much was bitcoin worth in 2015 compared to now"),
    ),
}


class TheMachineAnswersTheseItselfTests(unittest.TestCase):
    """route() on both floors over each shape and the live-data questions beside it."""

    def setUp(self):
        self.rec = _Recorded(self)

    def _route(self, lock, sentence):
        router, search, _model = _router(lock)
        before = len(self.rec.glass_rows("current_data_offer"))
        result = router.route(sentence)
        offered = len(self.rec.glass_rows("current_data_offer")) - before
        return router, search, result, offered

    def _check(self, shape):
        answered, live = SHAPES[shape]
        for lock in (True, False):
            for sentence in answered:
                with self.subTest(floor="locked" if lock else "native",
                                  sentence=sentence):
                    router, search, result, offered = self._route(lock, sentence)
                    self.assertNotEqual(result.source, "current_data_offer")
                    self.assertNotEqual(result.text, LOCKED_OFFER)
                    self.assertFalse(router._conv.pending_search_offer)
                    self.assertEqual((offered, search.ran), (0, []))
        for lock in FLOORS_THAT_OFFER:
            for sentence in live:
                with self.subTest(floor="locked" if lock else "native",
                                  live=sentence):
                    router, search, result, offered = self._route(lock, sentence)
                    self.assertEqual(result.source, "current_data_offer")
                    self.assertEqual(result.text, LOCKED_OFFER)
                    self.assertEqual(router._conv.pending_search_offer,
                                     sentence.strip())
                    self.assertEqual((offered, search.ran), (1, []))

    def test_a_unit_conversion_is_not_a_currency_conversion(self):
        self._check("a unit conversion")

    def test_a_how_to_is_taught_not_offered_a_search(self):
        self._check("a how-to")

    def test_a_task_over_supplied_text_is_not_a_live_data_question(self):
        self._check("a task over supplied text")

    def test_a_sum_over_a_stated_price_is_not_a_live_data_question(self):
        self._check("a sum over a stated price")

    def test_a_machine_state_question_belongs_to_the_system(self):
        self._check("a machine-state question")

    def test_a_past_dated_result_is_history(self):
        self._check("a past-dated result")


def _native(emits=None):
    """The native floor's router over a model that invents the figure when it
    is asked, recording every time it is asked."""
    router, search, model = _router(False, emits=emits)
    model["asked"] = 0
    offer_tools = router._llm.stream_with_tools

    def _stream_with_tools(messages, *, tools=(), **kwargs):
        model["asked"] += 1
        if emits is not None:
            yield from offer_tools(messages, tools=tools, **kwargs)
        else:
            model["offered"].append([getattr(t, "name", t) for t in (tools or [])])
            yield INVENTED

    def _stream(messages, **kwargs):
        model["asked"] += 1
        return iter([INVENTED])

    router._llm.stream_with_tools = _stream_with_tools
    router._llm.stream = _stream
    return router, search, model


class TheNativeFloorOffersLiveDataTests(unittest.TestCase):
    """route() on the native floor, the path the desktop bus drives."""

    def setUp(self):
        self.rec = _Recorded(self)

    def test_a_live_data_question_gets_the_offer_and_no_figure(self):
        router, search, model = _native()
        result = router.route(LIVE)
        self.assertEqual(result.text, LOCKED_OFFER)
        self.assertNotIn("$", result.text)
        self.assertEqual(result.source, "current_data_offer")
        self.assertEqual(model["asked"], 0, "the model was asked")
        self.assertEqual(search.ran, [])
        self.assertEqual(router._conv.pending_search_offer, LIVE)
        self.assertTrue(self.rec.glass_rows("current_data_offer"))

    def test_yes_runs_the_search_through_the_resolver(self):
        router, search, model = _native()
        router.route(LIVE)
        self.assertEqual(search.ran, [])
        router.route("yes")
        self.assertEqual(len(search.ran), 1, search.ran)
        self.assertIn("bitcoin", search.ran[0]["query"])
        word = router.turn_search_ask()
        self.assertEqual(getattr(word, "how", None), "accepted_offer")
        self.assertIn("bitcoin", getattr(word, "target", ""))
        self.assertTrue(self.rec.glass_rows("search_offer_consume"))
        self.assertEqual(model["asked"], 0, "the model was asked")

    def test_a_question_outside_the_class_routes_as_before(self):
        router, search, model = _native()
        result = router.route(NOT_LIVE)
        self.assertNotEqual(result.source, "current_data_offer")
        self.assertGreater(model["asked"], 0, "the model was not asked")
        self.assertEqual(search.ran, [])

    def test_an_explicit_ask_searches_without_an_offer(self):
        """The protection the removed guard still owes: an explicit ask is never
        offered (the literal phrasing fails out; the other word orders are
        declined by router._recognised_web_dispatch) and runs its search."""
        for sentence, _target in EXPLICIT_ASKS:
            with self.subTest(sentence=sentence):
                before = len(self.rec.glass_rows(
                    "current_data_offer_declined_for_dispatch"))
                router, search, _model = _native(emits=_model_search)
                result = router.route(sentence)
                self.assertNotEqual(result.text, LOCKED_OFFER)
                self.assertEqual(len(search.ran), 1, search.ran)
                self.assertFalse(self.rec.glass_rows("current_data_offer"))
                declined = len(self.rec.glass_rows(
                    "current_data_offer_declined_for_dispatch")) - before
                self.assertEqual(declined, 0 if sentence.startswith("search the web")
                                 else 1)


class TheLockedFloorAnswersAsBeforeTests(unittest.TestCase):
    """The locked floor: the same offer, the same yes, the same routes."""

    def setUp(self):
        self.rec = _Recorded(self)

    def test_a_live_data_question_gets_the_same_offer(self):
        router, search, model = _router(True)
        result = router.route(LIVE)
        self.assertEqual(result.text, LOCKED_OFFER)
        self.assertEqual(result.source, "current_data_offer")
        self.assertEqual((search.ran, model["offered"]), ([], []))
        router.route("yes")
        self.assertEqual(len(search.ran), 1, search.ran)

    def test_a_question_outside_the_class_gets_no_offer(self):
        router, search, _model = _router(True)
        result = router.route(NOT_LIVE)
        self.assertNotEqual(result.source, "current_data_offer")
        self.assertEqual(search.ran, [])

    def test_an_explicit_ask_searches_as_before(self):
        for sentence, target in EXPLICIT_ASKS:
            with self.subTest(sentence=sentence):
                router, search, model = _router(True)
                router.route(sentence)
                self.assertEqual(search.ran, [{"query": target}])
                self.assertEqual(model["offered"], [])


if __name__ == "__main__":
    unittest.main()
