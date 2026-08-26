# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An elliptical reply must be answered, not replayed.

THE FIELD CASE, 2026-08-24. A person asked how to care for overgrown blackberry
briars and was given a numbered procedure. She typed "wha?" — she had not
understood — and the same numbered procedure came back, word for word. A person
who did not understand an answer is asking for a DIFFERENT answer; the one
response that cannot help them is the same one again.

WHY THIS NEEDED A NEW ASSERTION. Every other assertion in the harness grades a
turn against itself: what it contains, which route answered it, which tool ran.
Not one of them can see the turn BEFORE it, so "never a replay" could not be
written down. `not_repeat_of_previous` is the only assertion that compares a
reply to the previous reply, and this file is what says it works — including
the negative direction, because an assertion that cannot fail grades nothing.
"""

from __future__ import annotations

import unittest

from intergen.tests.scenario import grader, schema
from intergen.tests.scenario.schema import Assertion, Scenario, Turn
from intergen.tests.scenario.transport import TurnResult

_PROCEDURE = (
    "To care for overgrown blackberry briars, follow these steps:\n"
    "1. Pruning: Prune the briars to maintain shape.\n"
    "2. Thinning: Remove the oldest canes at the base.\n"
    "3. Feeding: Apply a balanced fertiliser in early spring."
)


def _grade(reply: str, prior: str, value: str = ""):
    turn = Turn(user="wha?", assertions=[
        Assertion(type="not_repeat_of_previous", value=value,
                  description="the reply is not a replay")])
    return grader.grade_turn(
        turn, TurnResult(text=reply, source="llm"), None,
        category="conversational", prior_reply=prior)


def _only(tg):
    return next(r for r in tg.results if r.type == "not_repeat_of_previous")


class TheAssertionIsRegistered(unittest.TestCase):

    def test_the_schema_knows_the_type(self):
        self.assertIn("not_repeat_of_previous", schema.ASSERTION_TYPES)

    def test_the_grader_can_evaluate_it(self):
        self.assertIn("not_repeat_of_previous", grader._EXPLICIT_EVALUATORS)


class TheAssertionFails(unittest.TestCase):
    """The negative direction first: an assertion that cannot fail grades nothing."""

    def test_a_verbatim_replay_fails(self):
        r = _only(_grade(_PROCEDURE, _PROCEDURE))
        self.assertFalse(r.passed)
        self.assertIn("opening line", r.actual)

    def test_the_same_procedure_behind_a_new_preamble_fails(self):
        reply = "Sure — here it is again.\n" + _PROCEDURE
        r = _only(_grade(reply, _PROCEDURE))
        self.assertFalse(r.passed, "a replay with a fresh first line is still a replay")
        self.assertIn("came back", r.actual)

    def test_the_ceiling_is_honoured(self):
        """Two of four lines returned is 50% — under a 90% ceiling, over a 40% one."""
        partial = "Which part would you like me to expand?\n" \
                  "2. Thinning: Remove the oldest canes at the base."
        self.assertTrue(_only(_grade(partial, _PROCEDURE)).passed)
        self.assertFalse(_only(_grade(partial, _PROCEDURE, value="10")).passed)


class TheAssertionPasses(unittest.TestCase):

    def test_a_clarifying_question_passes(self):
        reply = ("Sorry — which part would you like me to go over again, the "
                 "pruning or the feeding?")
        self.assertTrue(_only(_grade(reply, _PROCEDURE)).passed)

    def test_a_genuinely_different_answer_passes(self):
        reply = ("Put simply: cut the old canes out at ground level in winter, "
                 "and leave this year's new growth alone.")
        self.assertTrue(_only(_grade(reply, _PROCEDURE)).passed)

    def test_the_first_turn_of_a_scenario_passes(self):
        """Nothing exists to repeat, so the assertion is typable on any turn."""
        self.assertTrue(_only(_grade("Anything at all.", "")).passed)


class ItIsWiredThroughAWholeScenario(unittest.TestCase):
    """grade_turn is not where a scenario is graded — grade_scenario is."""

    def test_grade_scenario_carries_the_previous_reply_forward(self):
        scenario = Scenario(
            id="FS-probe", name="elliptical reply", axis=["routing"],
            category="conversational",
            turns=[
                Turn(user="how do I care for overgrown blackberry briars?"),
                Turn(user="wha?", assertions=[
                    Assertion(type="not_repeat_of_previous",
                              description="answered, not replayed")]),
            ])
        results = [TurnResult(text=_PROCEDURE, source="llm"),
                   TurnResult(text=_PROCEDURE, source="llm")]
        sg = grader.grade_scenario(scenario, results)
        second = sg.turns[1]
        r = next(x for x in second.results
                 if x.type == "not_repeat_of_previous")
        self.assertFalse(
            r.passed,
            "grade_scenario did not carry the previous reply into the turn, so the "
            "replay was graded as if there were nothing before it")
        # WHY the reason is asserted and not just the verdict: a grader that does
        # not know this assertion type at all also reports it as not-passed, so a
        # bare assertFalse here goes green on a tree where the whole mechanism is
        # missing. Measured — that is exactly what it did at the base commit.
        self.assertTrue(
            "opening line" in r.actual or "came back" in r.actual,
            f"the failure must name the replay, not merely refuse the turn; "
            f"actual={r.actual!r}")

    def test_the_same_scenario_passes_when_the_reply_clarifies(self):
        scenario = Scenario(
            id="FS-probe-2", name="elliptical reply, answered", axis=["routing"],
            category="conversational",
            turns=[
                Turn(user="how do I care for overgrown blackberry briars?"),
                Turn(user="wha?", assertions=[
                    Assertion(type="not_repeat_of_previous",
                              description="answered, not replayed")]),
            ])
        results = [TurnResult(text=_PROCEDURE, source="llm"),
                   TurnResult(text="Which part was unclear — the pruning?",
                              source="llm")]
        sg = grader.grade_scenario(scenario, results)
        r = next(x for x in sg.turns[1].results
                 if x.type == "not_repeat_of_previous")
        self.assertTrue(r.passed)


if __name__ == "__main__":
    unittest.main()
