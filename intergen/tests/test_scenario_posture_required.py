# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A run that grades tier-specific assertions must say which tier it drove.

THE DEFECT, read in the tree. A scenario turn may carry assertions written for
different tiers, and those assertions can be MUTUALLY EXCLUSIVE — the corpus
turn used below asserts ``routes_via=llm_freeform`` under ``2B-locked`` and
``routes_via=llm_tools`` under ``9B-native`` for the same sentence, because the
locked tier answers freeform and the native tier decides tools. ``grade_turn``
skips a posture-gated assertion only when a posture is passed to it; with
``posture=None`` it evaluated every one of them, so on a real box exactly one of
each such pair had to fail no matter how the product behaved.

WHAT THAT PRODUCED. A whole-corpus run driven on one tier with no posture named
counted 31 failing assertions that were written for a tier the box is not, and
20 scenarios failed on nothing else. Nine of the ten scenarios that made the
memory-persistence axis read 0/10 were in that set; the product had answered
those turns correctly.

WHAT THIS FILE PINS. Grading a posture-gated assertion with no posture named is
refused, loudly, naming the assertion and the postures it was written for. It is
not silently skipped either: a skipped assertion would quietly shrink the
denominator and let a run report a coverage it never had. The caller must say
which tier it drove — a live run always knows.

THE CONTRACT THIS REPLACES. ``intergen/tests/test_scenario_posture.py``
previously pinned that ``posture=None`` evaluates every assertion, described as
back-compatibility. That is the behaviour above, written down as if it were
intended, so this change updates that case rather than working around it.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from intergen.tests.scenario.grader import (PostureNotNamed, grade_scenario,
                                            grade_turn)
from intergen.tests.scenario.loader import parse_scenario
from intergen.tests.scenario.schema import Assertion, Turn
from intergen.tests.scenario.transport import TurnResult

_CORPUS = (Path(__file__).resolve().parent / "scenario" / "corpus"
           / "memory_personal.json")


def _mem_cross_b01():
    """The real corpus scenario, loaded through the real loader."""
    raw = json.loads(_CORPUS.read_text(encoding="utf-8"))
    scenarios = raw if isinstance(raw, list) else raw.get("scenarios", [])
    for s in scenarios:
        if s.get("id") == "MEM-cross-b01":
            return parse_scenario(s, source=str(_CORPUS))
    raise AssertionError("MEM-cross-b01 is not in the corpus any more")


class GradingWithoutAPostureIsRefused(unittest.TestCase):
    """The turn that produced the false failures, graded three ways."""

    def setUp(self) -> None:
        self.scenario = _mem_cross_b01()
        self.recall_turn = self.scenario.turns[1]
        gated = [a for a in self.recall_turn.assertions if a.postures]
        self.assertGreaterEqual(
            len(gated), 2,
            "control: this corpus turn no longer carries the tier-specific "
            "assertions this case is about")

    def _reply(self) -> TurnResult:
        # The reply a locked 2B box actually gave for this turn: the stored
        # value, answered freeform.
        return TurnResult(text="Your backup drive is /dev/sdb1",
                          source="llm_freeform")

    def test_no_posture_is_refused_and_names_what_it_could_not_grade(self) -> None:
        with self.assertRaises(PostureNotNamed) as raised:
            grade_turn(self.recall_turn, self._reply(),
                       category=self.scenario.category, posture=None)
        message = str(raised.exception)
        self.assertIn("routes_via", message)
        self.assertIn("2B-locked", message)
        self.assertIn("9B-native", message)

    def test_the_tier_that_was_driven_passes(self) -> None:
        # Named as the tier the reply came from, the same reply grades PASS:
        # the product was right and only the measurement was wrong.
        grade = grade_turn(self.recall_turn, self._reply(),
                           category=self.scenario.category,
                           posture="2B-locked")
        routes = [r for r in grade.results if r.type == "routes_via"]
        self.assertEqual(len(routes), 1,
                         "only the assertion written for this tier applies")
        self.assertTrue(routes[0].passed)

    def test_the_other_tier_is_graded_on_its_own_terms(self) -> None:
        native = TurnResult(text="Your backup drive is /dev/sdb1",
                            source="llm_tools")
        grade = grade_turn(self.recall_turn, native,
                           category=self.scenario.category,
                           posture="9B-native")
        routes = [r for r in grade.results if r.type == "routes_via"]
        self.assertEqual(len(routes), 1)
        self.assertTrue(routes[0].passed)


class TheRefusalReachesTheScenarioLevel(unittest.TestCase):
    def test_grade_scenario_refuses_too(self) -> None:
        scenario = _mem_cross_b01()
        replies = [TurnResult(text="Got it. I'll remember: backup drive",
                              source="memory"),
                   TurnResult(text="Your backup drive is /dev/sdb1",
                              source="llm_freeform")]
        with self.assertRaises(PostureNotNamed):
            grade_scenario(scenario, replies, posture=None)


class AnUngatedTurnIsUnaffected(unittest.TestCase):
    """Control: nothing changes for a turn with no tier-specific assertion."""

    def test_no_posture_still_grades(self) -> None:
        turn = Turn(user="what time is it",
                    assertions=[Assertion("contains", "ok")])
        grade = grade_turn(turn, TurnResult(text="ok here"), posture=None)
        self.assertEqual(grade.grade, "PASS")


if __name__ == "__main__":
    unittest.main()
