# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""WP-3.2 — judge annotation layer: structural-first, calibration floor.

Pins the two invariants that keep honesty structural: (1) the judge ANNOTATES a
run and never changes its Gate-A grade, and (2) the deterministic Layer-1 screen,
driven through the SAME scenario bridge, catches the RED known-garbage fixture —
the pre-RC calibration floor. No daemon and no model: Layer 1 is deterministic.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from intergen.tests.scenario.judge import (
    FORBIDDEN_JUDGE_FAMILY,
    annotate_run,
    annotate_turn,
    calibration_catches,
    run_worst_verdict,
    screen_calibration_seed,
    worst_verdict,
)
from intergen.tests.scenario.report import build_results
from intergen.tests.scenario.runner import run_scenario
from intergen.tests.scenario.schema import Assertion, Scenario, Turn
from intergen.tests.scenario.transport import MockTransport, TurnResult

_SEEDS = json.loads(
    (Path(__file__).resolve().parent / "judge_calibration" /
     "known_garbage_seeds.json").read_text())["seeds"]


class AnnotationBridgeTests(unittest.TestCase):
    def test_apology_reoffer_is_flagged(self):
        # A single-turn apology re-offer (no antecedent) flags, not fails.
        r = TurnResult(text="I'm sorry. Would you like me to try again?")
        verdicts = {v.dimension: v.verdict for v in annotate_turn("do it", r)}
        self.assertEqual(verdicts.get("not_asshole"), "flag")

    def test_clean_answer_annotates_empty(self):
        r = TurnResult(text="Your default editor is neovim.")
        self.assertEqual(annotate_turn("what's my editor?", r), [])
        self.assertEqual(worst_verdict(annotate_turn("q", r)), "pass")


class StructuralFirstTests(unittest.TestCase):
    """The core §5.3 invariant: the judge annotates; Gate A decides the grade."""

    def test_gate_a_pass_survives_a_judge_flag(self):
        # The answer satisfies the Gate-A contains assertion (grade PASS) but is a
        # tonal apology re-offer the judge flags. The grade must NOT change.
        s = Scenario(id="S", name="S", axis=["routing"],
                     turns=[Turn(user="what's my editor?",
                                 assertions=[Assertion("contains", "neovim")])])
        garbage_but_grounded = TurnResult(
            text="I'm sorry, my bad. Would you like me to look again? It's neovim.")
        t = MockTransport(replies={"what's my editor?": garbage_but_grounded})
        run = run_scenario(s, t)
        self.assertEqual(run.grade.grade, "PASS")           # Gate A unaffected
        annotations = annotate_run(run, s)
        self.assertEqual(run_worst_verdict(annotations), "flag")  # judge still flags
        self.assertEqual(run.grade.grade, "PASS")           # still unchanged after annotate

    def test_annotations_ride_alongside_in_results(self):
        s = Scenario(id="S", name="S", axis=["routing"],
                     turns=[Turn(user="q", assertions=[Assertion("contains", "ok")])])
        t = MockTransport(replies={"q": TurnResult(text="ok, my bad, shall I retry?")})
        run = run_scenario(s, t)
        from intergen.tests.scenario.judge import annotations_to_dict
        ann = {run.scenario_id: annotations_to_dict(annotate_run(run, s))}
        results = build_results([run], [s], "r", judge_annotations=ann)
        scen = results["scenarios"][0]
        self.assertEqual(scen["grade"], "PASS")             # structural grade stands
        self.assertIn("judge", scen)                        # annotation rides alongside


class CalibrationFloorTests(unittest.TestCase):
    """The deterministic screen, through the scenario bridge, must flag the RED
    known-garbage the fixture marks deterministic — the pre-RC floor."""

    def test_deterministic_known_garbage_caught_via_bridge(self):
        red = [s for s in _SEEDS
               if s.get("class") == "known_garbage" and s.get("deterministic")]
        self.assertGreaterEqual(len(red), 3)  # fixture actually carries some
        for seed in red:
            with self.subTest(seed=seed["id"]):
                self.assertTrue(calibration_catches(seed),
                                f"{seed['id']} not caught by the deterministic floor")

    def test_known_good_not_hard_failed(self):
        for seed in [s for s in _SEEDS if s.get("class") == "known_good"]:
            with self.subTest(seed=seed["id"]):
                verdicts = screen_calibration_seed(seed)
                self.assertNotIn("fail", verdicts.values(),
                                 f"{seed['id']} wrongly hard-failed")


class JudgeFamilyGuardTests(unittest.TestCase):
    def test_judge_family_differs_from_the_assistant(self):
        # The assistant is the forbidden family; the judge default must not be it.
        from intergen.tests.scenario.judge import DEFAULT_JUDGE_MODEL
        self.assertNotIn(FORBIDDEN_JUDGE_FAMILY, DEFAULT_JUDGE_MODEL)


if __name__ == "__main__":
    unittest.main()
