# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Output that is not language is a judge FAIL, not a flag.

The measured defect (2026-08-07, two independent anchor rounds at stability
1.0000): asked whether NetworkManager was running, the model answered with 900
characters of dashes and the word "no". The judge's own reasoning called it "a
random collection of symbols and Chinese characters", and it scored the turn
'flag'. A reply with no claim in it is maximally un-checkable, and the rubric
tells the judge to answer 'flag' when a claim cannot be checked — so the softest
verdict landed on the worst output.

WHY THIS IS DETERMINISTIC AND NOT PROMPT TEXT, stated because the alternative was
tried and measured: two rubric-text variants were fired against the live judge.
The first moved 19 of the other 48 anchors and collapsed the judge's use of
'flag' from 20 to 4; the second moved 12 and did not even reach the target. The
judge is deterministic — two identical rounds moved ZERO items, stability 1.0000
— but extremely sensitive to prompt weight. So the rule is applied in Layer 1,
where it is exact, and the judge prompt is left byte-identical to what it was.

The predicate is the SHARED one the serving floor and the bring-up coherence gate
already use (intergen.coherence.degeneracy_reason), calibrated against the same
441 sealed baseline replies the anchor set was drawn from. A third definition of
"not language" in this tree could drift from the two that gate real serving.

Pure data — no judge, no network, no daemon.
"""

from __future__ import annotations

import unittest

from intergen.coherence import degeneracy_reason
from intergen.tests import quality_judge as qj
from intergen.tests.quality_judge import (
    JudgeInputs, compose_overall, deterministic_screen, judge_turn,
)


def _inputs(text: str, *, user: str = "Is the NetworkManager service running?"):
    return JudgeInputs(user_input=user, assembled_prompt="",
                       model_output=text, delivered=text, source="")


# Written here to the three measured shapes, NOT copied from the sealed capture
# set: the frozen replies are the instrument that measures judge drift, and an
# instrument published in the tree under test is an instrument that can be
# trained against. Each fixture below is checked against the shared predicate in
# its own test, so a fixture that stopped tripping the branch it names would be
# caught here rather than silently weakening the cell.
SMEAR = ("----------no--------no--no------------------no---------no------------"
         "------------------no----------------no-------------no---no----------"
         "-----no--------------------------no---------------no--\n\n--no--")
SCRIPT_SALAD = ("：：：：对：对：：对对：：：：：：：：：：：对：：：：：：：：-：：对：：："
                "：：对：对-对：：：：：：：对：对：：：：：：：：：：对对：-对：：：对：对：：对：")
CHAR_LOOP = "running " * 40 + "running running yes running"
SHORT_NON_WORD = "gronk"
GOOD = "NetworkManager is active and running."
GOOD_WITH_TABLE = ("Here is the current disk usage:\n\n```\n"
                   "/dev/nvme0n1p2  916G  201G  669G  24% /\n```\n"
                   "You have 669 GB free, about 76% of the disk.")


class NonLinguisticOutputFailsTests(unittest.TestCase):
    """The behavioural red: at base these graded 'flag', not 'fail'."""

    def test_the_fixtures_trip_the_branches_they_are_named_for(self):
        # The fixtures are written here rather than copied from the sealed set,
        # so their reason strings are pinned: a fixture that drifted into a
        # different branch (or stopped firing) would otherwise make the cells
        # below pass for the wrong reason.
        self.assertIn("punctuation smear", degeneracy_reason(SMEAR) or "")
        self.assertIn("punctuation smear", degeneracy_reason(SCRIPT_SALAD) or "")
        self.assertIn("character-level repetition",
                      degeneracy_reason(CHAR_LOOP) or "")

    def test_a_punctuation_smear_is_a_deterministic_fail(self):
        screened = deterministic_screen(_inputs(SMEAR))
        hits = [d for d in screened if d.dimension == "correct"]
        self.assertEqual(len(hits), 1,
                         "the screen produced no verdict for output that is not language")
        self.assertEqual(hits[0].verdict, "fail")
        self.assertIn("not language", hits[0].evidence)

    def test_the_overall_verdict_is_fail_not_flag(self):
        # The whole point: 'flag' on this input is the defect.
        self.assertEqual(judge_turn(_inputs(SMEAR)).overall, "fail")

    def test_a_script_salad_is_a_fail(self):
        self.assertEqual(judge_turn(_inputs(SCRIPT_SALAD)).overall, "fail")

    def test_a_character_level_loop_is_a_fail(self):
        # The other branch of the shared predicate. One of the measured replies
        # that this change moves is a repetition loop, not a smear, so both
        # branches have to reach the same verdict.
        self.assertEqual(judge_turn(_inputs(CHAR_LOOP)).overall, "fail")

    def test_it_lands_on_a_substance_dimension_so_it_cannot_be_capped(self):
        # compose_overall caps a STYLE 'fail' from the LLM to 'flag'. If this
        # verdict were placed on a style dimension it would be capped and the
        # defect would survive the fix.
        self.assertIn("correct", qj.INCOHERENCE_DIMENSIONS)
        self.assertNotIn("correct", qj.STYLE_DIMENSIONS)
        screened = deterministic_screen(_inputs(SMEAR))
        dims = {d.dimension: d for d in screened}
        self.assertEqual(compose_overall(dims, screened), "fail")

    def test_a_layer_one_fail_survives_an_llm_that_says_pass(self):
        # Layer 1 overrides the LLM per dimension. A judge that scores the smear
        # clean must not be able to rescue it.
        def all_pass(_prompt: str) -> str:
            import json
            return json.dumps({"reasoning": "looks fine to me", "dimensions": {
                d.id: {"verdict": "pass", "evidence": "x"}
                for d in qj.RUBRIC_DIMENSIONS}})
        self.assertEqual(
            judge_turn(_inputs(SMEAR), judge_client=all_pass).overall, "fail")


class RealAnswersAreUntouchedTests(unittest.TestCase):
    """The controls. A check that fired on these would condemn correct behaviour."""

    def test_a_normal_answer_is_not_screened(self):
        self.assertEqual(
            [d for d in deterministic_screen(_inputs(GOOD))
             if d.dimension == "correct"], [])

    def test_an_answer_that_shows_a_command_table_is_not_screened(self):
        # Fenced output is legitimately non-linguistic; the shared predicate
        # removes fences before measuring, and this pins that it stays true here.
        self.assertEqual(
            [d for d in deterministic_screen(_inputs(GOOD_WITH_TABLE))
             if d.dimension == "correct"], [])

    def test_a_short_legitimate_answer_is_not_screened(self):
        for short in ("12", "1989", "Yes", "No", "intergenos-dev", "Brasilia"):
            self.assertEqual(
                [d for d in deterministic_screen(_inputs(short))
                 if d.dimension == "correct"], [], short)

    def test_the_screen_uses_the_shared_predicate_not_a_second_definition(self):
        # If this module ever grew its own notion of "not language", the serving
        # floor and the judge could disagree about the same reply.
        self.assertIs(qj.degeneracy_reason, degeneracy_reason)


class TheShortNonWordGapIsNamedTests(unittest.TestCase):
    """What this change does NOT reach, pinned so it cannot be forgotten.

    The other measured failure shape is a five-character non-word answering a
    question about a package. The shared predicate abstains below 40 characters
    by its own calibration — at that length its character-class signals carry no
    information — so this cell is honest about the gap rather than papering over
    it with a second, uncalibrated rule.

    Nor could this screen honestly reach that shape. Character classes cannot
    separate a made-up five-letter token from a real package name, which is also
    a five-letter token that is not a dictionary word; the distinguishing fact is
    whether the named package exists, which is a claim-verification check against
    real package data, not a "this is not language" check. If a future change
    closes the gap, this test is what will say so.
    """

    def test_the_shared_predicate_abstains_on_a_short_non_word(self):
        self.assertIsNone(degeneracy_reason(SHORT_NON_WORD))

    def test_and_therefore_the_screen_does_not_catch_it(self):
        self.assertEqual(
            [d for d in deterministic_screen(_inputs(SHORT_NON_WORD))
             if d.dimension == "correct"], [],
            "if this now fires, the short-non-word gap has been closed — update "
            "the delivery's unproven-residue statement")


if __name__ == "__main__":
    unittest.main()
