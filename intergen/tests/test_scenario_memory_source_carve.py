# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The memory-source carve + claim-contract routing assertions.

Two mechanisms, one principle: an assertion must state the contract the
architecture implements, and a guard must fault a value for its PROVENANCE, not
for its shape.

* The fabrication guards (``no_invented_artifact`` /
  ``no_hallucinated_device_path``) faulted a device path on shape alone, so a
  CORRECT cross-session recall of a stored path hard-failed while the
  ``contains`` assertion for the same string passed. Recalled is not invented.
  The carve keys on where the value came from; a path with no attested source
  still fails hard, which is what keeps the guards load-bearing.
* ``routes_via`` pinned system-state turns to a source by POSTURE, but the state
  cache and the system map are read BEFORE the dispatch-lockdown check in
  ``router.py``, so those intercepts fire posture-invariantly. Where the handler
  depends on the DATA as well as the query, ``routes_via_any`` states the real
  disjunction instead of a source the architecture never guarantees.
"""

from __future__ import annotations

import unittest

from intergen.tests.scenario.grader import (
    grade_scenario,
    grade_turn,
    literal_provenance,
)
from intergen.tests.scenario.loader import ScenarioValidationError, parse_scenario
from intergen.tests.scenario.schema import Assertion, Scenario, Turn
from intergen.tests.scenario.transport import TurnResult

STORE = "remember that my backup drive is /dev/sdb1"
RECALL = "what's my backup drive?"


def _result(text: str, source: str = "llm_freeform") -> TurnResult:
    return TurnResult(text=text, source=source, handled=True)


def _persistence_scenario() -> Scenario:
    """The shape of a cross-session persistence fixture: store, then recall."""
    return Scenario(
        id="T-cross", name="store then recall", axis=["memory_persistence"],
        category="memory", postures=["2B-locked", "9B-native"],
        session_policy="multi-session",
        turns=[
            Turn(user=STORE, assertions=[Assertion("routes_via", "memory")]),
            Turn(user=RECALL, session_marker="restart-before", assertions=[
                Assertion("contains", "/dev/sdb1"),
                Assertion("no_invented_artifact"),
            ]),
        ],
    )


class LiteralProvenanceTests(unittest.TestCase):
    """Provenance is about the value's SOURCE, never about how plausible it looks."""

    def test_value_the_question_supplied_is_sourced(self):
        self.assertEqual(
            literal_provenance("/dev/sdb1", STORE, "", "llm_freeform"), "question")

    def test_value_an_earlier_turn_supplied_is_sourced(self):
        self.assertEqual(
            literal_provenance("/dev/sdb1", RECALL, STORE, "llm_tools"), "conversation")

    def test_durable_store_route_attests_its_own_answer(self):
        self.assertEqual(
            literal_provenance("/dev/sdb1", RECALL, "", "memory"), "durable_store")

    def test_value_with_no_source_has_no_provenance(self):
        self.assertEqual(
            literal_provenance("/dev/nvme0n1p3", RECALL, STORE, "llm_tools"), "")

    def test_empty_literal_is_never_sourced(self):
        self.assertEqual(literal_provenance("", RECALL, STORE, "memory"), "")


class FabricationGuardCarveTests(unittest.TestCase):
    """Red and green, both directions, on the two guards the carve touches."""

    def _failures(self, text: str, source: str = "llm_tools") -> list[str]:
        grade = grade_scenario(
            _persistence_scenario(),
            [_result("Got it. I'll remember: **backup drive** = /dev/sdb1", "memory"),
             _result(text, source)])
        return [r.type for r in grade.turns[1].results if not r.passed]

    def test_correct_cross_session_recall_passes_both_guards(self):
        # The defect this carve fixes: this turn used to hard-fail both guards
        # while its own `contains` assertion passed.
        self.assertEqual(self._failures("Your backup drive is /dev/sdb1."), [])

    def test_fabricated_path_still_hard_fails(self):
        failures = self._failures("Your backup drive is /dev/nvme0n1p3.")
        self.assertIn("no_invented_artifact", failures)
        self.assertIn("no_hallucinated_device_path", failures)

    def test_near_miss_sibling_of_the_stored_path_still_hard_fails(self):
        # /dev/sdb2 is one character from the stored value and entirely plausible.
        # Plausibility is not provenance.
        failures = self._failures("Your backup drive is /dev/sdb2.")
        self.assertIn("no_invented_artifact", failures)
        self.assertIn("no_hallucinated_device_path", failures)

    def test_store_turn_echoing_the_users_own_path_passes(self):
        grade = grade_scenario(
            _persistence_scenario(),
            [_result("Got it — /dev/sdb1 noted.", "memory"),
             _result("Your backup drive is /dev/sdb1.", "llm_tools")])
        self.assertEqual([r.type for r in grade.turns[0].results if not r.passed], [])

    def test_a_turn_graded_standalone_has_no_conversation_provenance(self):
        # Without the scenario walk there is no prior text, so an unsourced path
        # is still faulted — the carve never assumes provenance it cannot see.
        turn = _persistence_scenario().turns[1]
        grade = grade_turn(turn, _result("Your backup drive is /dev/sdb1."),
                           category="memory")
        self.assertIn("no_invented_artifact",
                      [r.type for r in grade.results if not r.passed])

    def test_an_invented_path_beside_a_recalled_one_still_fails(self):
        # Both guards scan EVERY device path: a fabricated sibling must not ride
        # behind a legitimately recalled one in the same reply.
        failures = self._failures("Your drives are /dev/sdb1 and /dev/sdc9.")
        self.assertIn("no_hallucinated_device_path", failures)
        self.assertIn("no_invented_artifact", failures)

    def test_failure_text_names_the_sourced_values_it_did_not_fault(self):
        grade = grade_scenario(
            _persistence_scenario(),
            [_result("Got it.", "memory"),
             _result("Your drives are /dev/sdb1 and /dev/sdc9.", "llm_tools")])
        invented = next(r for r in grade.turns[1].results
                        if r.type == "no_invented_artifact")
        self.assertFalse(invented.passed)
        self.assertIn("/dev/sdc9", invented.actual)


class RoutesViaAnyTests(unittest.TestCase):
    """A disjunction for handlers the architecture picks from query AND data."""

    def _turn(self, value: str) -> Turn:
        return Turn(user="What GPU do I have?",
                    assertions=[Assertion("routes_via_any", value)])

    def test_any_listed_source_satisfies(self):
        for source in ("cache", "llm_tools"):
            grade = grade_turn(self._turn("cache,llm_tools"),
                               _result("An adapter is present.", source))
            self.assertTrue(
                next(r for r in grade.results if r.type == "routes_via_any").passed,
                f"{source} should satisfy the disjunction")

    def test_a_source_outside_the_set_fails_hard(self):
        grade = grade_turn(self._turn("cache,llm_tools"),
                           _result("An adapter is present.", "identity"))
        result = next(r for r in grade.results if r.type == "routes_via_any")
        self.assertFalse(result.passed)
        self.assertIn("identity", result.actual)
        self.assertEqual(result.gate, "A")

    def test_loader_refuses_a_single_source_disjunction(self):
        with self.assertRaises(ScenarioValidationError):
            parse_scenario({
                "id": "T", "name": "t", "axis": ["routing"],
                "turns": [{"user": "q", "assertions": [
                    {"type": "routes_via_any", "value": "cache"}]}]})


class AssertionGateOverrideTests(unittest.TestCase):
    """A per-assertion re-scope is visible in the fixture, never silent."""

    def _scenario(self, assertion: dict) -> dict:
        return {"id": "T", "name": "t", "axis": ["routing"],
                "turns": [{"user": "q", "assertions": [assertion]}]}

    def test_phrasing_rescope_reports_mixed_instead_of_hard_failing(self):
        turn = Turn(user="q", assertions=[
            Assertion("contains_any", "then,next", gate="B",
                      description="phrasing variance, not routing")])
        grade = grade_turn(turn, _result("Here is what to do."))
        self.assertEqual(grade.gate_a, "PASS")
        self.assertEqual(grade.grade, "MIXED")

    def test_override_without_a_reason_is_refused(self):
        with self.assertRaises(ScenarioValidationError):
            parse_scenario(self._scenario(
                {"type": "contains_any", "value": "then,next", "gate": "B"}))

    def test_unknown_gate_value_is_refused(self):
        with self.assertRaises(ScenarioValidationError):
            parse_scenario(self._scenario(
                {"type": "contains_any", "value": "then,next", "gate": "soft",
                 "description": "why"}))

    def test_absent_override_keeps_the_gate_the_type_implies(self):
        turn = Turn(user="q", assertions=[Assertion("contains_any", "then,next")])
        grade = grade_turn(turn, _result("Here is what to do."))
        self.assertEqual(grade.gate_a, "FAIL")


if __name__ == "__main__":
    unittest.main()
