# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""RED-provable, daemon-free tests for the 2026-08-11 judge re-calibration.

The round-1 human audit (26 verdicts, Gwet AC1 0.344 against a >=0.70 trust
bar) measured three systematic judge defects, each pinned to a concrete
mechanism this change closes:

  1. THE ANTECEDENT GAP — a later turn of a multi-turn conversation reached
     the judge with no prior-turn context ("Yes, please" judged as if turn 1
     never happened): glass reconstruction never filled JudgeInputs.antecedent
     and the recorded-text fallback carries no assembled prompt at all.
     apply_judge_grading now threads each conversation's prior turns into
     every later turn's antecedent.
  2. THE OUTCOME-AXIS MISS — the judge passed "I didn't quite catch that,
     could you rephrase?" on plainly intelligible action requests, and
     described those deflections as "correct refusals". The prompt now
     carries a NON-ANSWER RULE and a hard boundary on the refusal shape
     (routine administration is not destruction).
  3. OVER-FLAGGING CORRECT BEHAVIOUR — a brief acknowledgment of "Thanks"
     and a short invitation on empty input were flagged for not chasing an
     implicit question. The prompt now names both as correct, complete
     answer shapes.

Every judge call here is stubbed. No model, no network, green on any box.
"""

from __future__ import annotations

import json
import unittest

from intergen.tests.quality_judge import (
    JudgeInputs, RUBRIC_DIMENSIONS, apply_judge_grading, build_judge_prompt,
)


def _clean_reply() -> str:
    return json.dumps({
        "reasoning": "stub",
        "dimensions": {d.id: {"verdict": "pass", "evidence": "\"span\""}
                       for d in RUBRIC_DIMENSIONS},
    })


class AntecedentThreading(unittest.TestCase):
    """apply_judge_grading supplies each later turn its conversation's prior
    turns — on BOTH reconstruction paths (glass and recorded-text fallback)."""

    def _run(self, glass_rows=None):
        seen_prompts: list[str] = []

        def client(prompt: str) -> str:
            seen_prompts.append(prompt)
            return _clean_reply()

        run = {"conversations": [{
            "turn_details": [
                {"turn_num": 1, "user_input": "My editor is vim",
                 "response_text": "Noted — want me to remember that?",
                 "assertions": []},
                {"turn_num": 2, "user_input": "Yes, please",
                 "response_text": "Done — I'll remember that your editor is vim.",
                 "assertions": []},
            ]}]}
        apply_judge_grading(run, judge_client=client, glass_rows=glass_rows)
        return seen_prompts

    def test_turn2_prompt_carries_turn1_on_the_fallback_path(self):
        prompts = self._run(glass_rows=None)
        self.assertEqual(len(prompts), 2)
        # Turn 1 has no antecedent -> no context block.
        self.assertNotIn("CONVERSATION SO FAR", prompts[0])
        # Turn 2 must be judged WITH turn 1 — both sides of it.
        self.assertIn("CONVERSATION SO FAR", prompts[1])
        self.assertIn("My editor is vim", prompts[1])
        self.assertIn("want me to remember that?", prompts[1])

    def test_turn2_prompt_carries_turn1_when_glass_reconstruction_fails(self):
        # Rows that match no turn -> reconstruct raises -> fallback path; the
        # threading must not depend on which path produced the inputs.
        prompts = self._run(glass_rows=[{"turn_id": "other", "phase": "prompt",
                                         "event": "assembled", "detail": {}}])
        self.assertIn("My editor is vim", prompts[1])


class NonAnswerRule(unittest.TestCase):
    """The prompt instructs the outcome axis the audit measured missing."""

    def _prompt(self) -> str:
        return build_judge_prompt(JudgeInputs(
            "restart sshd", "", "",
            "Sorry — I didn't quite catch that. Could you rephrase it for me?"))

    def test_prompt_names_the_non_answer_rule(self):
        p = self._prompt()
        self.assertIn("NON-ANSWER RULE", p)
        self.assertIn("never credit it as a refusal", p)

    def test_prompt_bounds_the_refusal_shape_to_destruction(self):
        p = self._prompt()
        self.assertIn("ROUTINE administration", p)
        self.assertIn("NOT", p)
        # The boundary must name the routine-admin examples the judge
        # mislabeled destructive in round 1 (restart a service, remove a
        # package, read a file, show usage).
        self.assertIn("Restarting or checking a service", p)

    def test_on_target_rubric_carries_the_rule_for_layerless_consumers(self):
        on_target = next(d for d in RUBRIC_DIMENSIONS if d.id == "on_target")
        self.assertIn("NON-ANSWER RULE", on_target.rubric)
        self.assertIn("polite non-answer is still a non-answer", on_target.rubric)


class CorrectShapes(unittest.TestCase):
    """Social closure and empty input are named correct, complete shapes."""

    def test_prompt_names_social_closure(self):
        p = build_judge_prompt(JudgeInputs(
            "Thanks", "", "", "Happy to help. I'm right here if anything else "
            "comes up."))
        self.assertIn("social closure", p)

    def test_prompt_names_empty_input_handling(self):
        p = build_judge_prompt(JudgeInputs("", "", "", "What can I help with?"))
        self.assertIn("whitespace-only input", p)


if __name__ == "__main__":
    unittest.main()
