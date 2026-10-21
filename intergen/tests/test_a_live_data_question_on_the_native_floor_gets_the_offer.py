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
"""

from __future__ import annotations

import unittest

from intergen.tests.test_a_search_the_person_did_not_ask_for_is_offered_not_run import (
    LOCKED_OFFER, _Recorded, _router)

#: The floors that make the offer: the locked floor (True).
FLOORS_THAT_OFFER = (True,)

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


if __name__ == "__main__":
    unittest.main()
