# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A whole-machine overview ask is answered from the machine, not from the model.

Measured gap: "Give me a status overview of my system." matched no system-map
predicate and rode to the model tier, while every sibling phrasing of the same
question — "system status", "system health", "how is my system", the bare
"status report" — was claimed by the grounded map. The phrase list missed it
because it enumerates literal substrings and this shape puts a noun between the
state word and the machine ("status OVERVIEW of my system"); the bare objectless
branch missed it because the ask is not bare.

Decided 2026-07-25: it IS claimed. The ask is identical in meaning to its
claimed siblings and identical in what a correct answer needs — live state the
model tier does not have — so leaving it uncovered meant the same question was
answered from real data or from nothing depending only on the user's wording,
with no signal saying which had happened.

The claim is scoped to WHOLE-MACHINE overviews. These fixtures pin both edges:
what the predicate must now claim, and what it must still leave alone (a topic
overview, a named device, a report with a non-machine object).
"""

from __future__ import annotations

import unittest

from intergen.router import ConversationRouter


class WholeMachineOverviewIsClaimed(unittest.TestCase):

    def setUp(self) -> None:
        self.r = ConversationRouter.__new__(ConversationRouter)

    CLAIMED = [
        "Give me a status overview of my system.",   # the measured turn
        "give me an overview of my system",
        "can I get a system overview",
        "give me a status summary",
        "health summary please",
        "give me a rundown of my machine",
        "state snapshot of this computer",
    ]

    NOT_CLAIMED = [
        "give me an overview of pkm",                # a topic, not the machine
        "give me an overview of the wiki",
        "give me a status report for the meeting",   # non-machine object
        "give me a status overview of my printer",   # named device -> own check
        "what's the weather forecast",
    ]

    def test_the_overview_phrasings_are_claimed(self) -> None:
        for query in self.CLAIMED:
            with self.subTest(query=query):
                self.assertTrue(
                    self.r._is_system_map_query(query),
                    "a whole-machine overview ask fell through to the model tier")

    def test_a_non_machine_overview_is_left_alone(self) -> None:
        for query in self.NOT_CLAIMED:
            with self.subTest(query=query):
                self.assertFalse(
                    self.r._is_system_map_query(query),
                    "the overview predicate claimed a question that is not "
                    "about this machine's live state")

    def test_the_previously_claimed_siblings_are_unchanged(self) -> None:
        """The claim contract this ask is being reconciled TO."""
        for query in ("system status", "system health", "how is my system",
                      "status report", "is everything ok", "what's failing"):
            with self.subTest(query=query):
                self.assertTrue(self.r._is_system_map_query(query))


if __name__ == "__main__":
    unittest.main()
