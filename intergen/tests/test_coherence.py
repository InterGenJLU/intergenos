# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Coherence checker — the shape-based model-health predicate (intergen.coherence).

These pin the six checks that let the EARN-OFFLOAD gate (and the follow-on
semantic-health detector) tell coherent output from the Intel-ANV-F16 salad class
WITHOUT byte-comparing backends. Each check is exercised in isolation, plus the
two non-regression cases that must NEVER false-DENY: a coherent factual answer and
a coherent pure-numeric counting answer (zero foreign script, many distinct
tokens).

Check 6 (not degenerate) and its two primitives are also exercised directly,
because the serving-floor quality gate (intergen.llm.LLMRouter.check_quality)
consumes degeneracy_reason() rather than deriving its own — the reuse this
module's docstring mandates. Its false-positive direction is pinned here as
well as in test_llm_degenerate_output.py: a fenced table and a symbol-dense but
real answer must pass.
"""
from __future__ import annotations

import unittest

from intergen.coherence import (
    assess_coherence, compression_ratio, degeneracy_reason, nonalnum_share,
)


class CoherenceTests(unittest.TestCase):
    def test_coherent_factual_answer_passes(self) -> None:
        r = assess_coherence(
            "Paris", prompt_text="What is the capital of France?",
            expected_keywords=["Paris"])
        self.assertTrue(r.ok, r.reason)

    def test_coherent_counting_answer_passes(self) -> None:
        # Pure integers 1..300-ish: zero foreign script, no consecutive repeats,
        # low dominance — the long-leg shape a GRANT must accept.
        nums = " ".join(str(n) for n in range(1, 301))
        r = assess_coherence(nums, prompt_text="Count from 1 to 300",
                             expected_keywords=["150", "300"])
        self.assertTrue(r.ok, r.reason)
        self.assertLess(r.metrics["foreign_script_ratio"], 0.01)

    def test_empty_output_fails_substance(self) -> None:
        r = assess_coherence("   ", prompt_text="x", expected_keywords=[])
        self.assertFalse(r.ok)
        self.assertFalse(r.checks["substance"])

    def test_missing_expected_keyword_fails(self) -> None:
        r = assess_coherence("London", prompt_text="capital of France?",
                             expected_keywords=["Paris"])
        self.assertFalse(r.ok)
        self.assertFalse(r.checks["expected_keyword"])

    def test_foreign_script_salad_fails(self) -> None:
        # CJK spray with no expected keyword declared — must be caught by the
        # foreign-script ratio, not only by the keyword check.
        salad = "系统提示 你好 世界 语言 模型 " * 8
        r = assess_coherence(salad, prompt_text="Count from 1 to 300",
                             expected_keywords=[])
        self.assertFalse(r.ok)
        self.assertFalse(r.checks["foreign_script"])
        self.assertGreater(r.metrics["foreign_script_ratio"], 0.20)

    def test_verbatim_prompt_echo_fails(self) -> None:
        prompt = "You are a helpful assistant. Answer concisely."
        r = assess_coherence(
            prompt + " and now the output continues onward",
            prompt_text=prompt, expected_keywords=[])
        self.assertFalse(r.ok)
        self.assertFalse(r.checks["no_prompt_echo"])

    def test_repetition_blowup_fails(self) -> None:
        r = assess_coherence("the the the the the the the the the the the",
                             prompt_text="x", expected_keywords=[])
        self.assertFalse(r.ok)
        self.assertFalse(r.checks["no_repetition"])

    def test_token_dominance_fails(self) -> None:
        # One token dominating a long output (a compute collapse that is not a
        # simple adjacent run) is still caught.
        out = "a b a a c a a d a a e a a f a a g a a h a a".replace(" ", " ")
        r = assess_coherence(out, prompt_text="x", expected_keywords=[])
        self.assertFalse(r.ok)
        self.assertFalse(r.checks["no_repetition"])

    def test_punctuation_smear_fails_the_degeneracy_check(self) -> None:
        # The shape the whitespace-token repetition check cannot see: every
        # punctuation cluster is a distinct token, so check 5 passes it. This is
        # the opening 55 non-whitespace characters of a reply the 2B tier
        # actually served on 2026-08-07 (trace c215bca41ac7, "get me htop").
        smear = ('"""""，""""##""\n\n"-" \n\n\n\n\n<\nn"\n\n\n\n<\n"\n\n"\n"\n"'
                 '\n####\n\n""##\n、"\n""\n\n"、 \n\n"， \n""\n"\n\n""\n，##  \n  '
                 '\n\n\n \n####\n\n\n\n\n \n\n\n\n\n\n\n\n，\n\n\n\n\n"')
        r = assess_coherence(smear, prompt_text="get me htop",
                             expected_keywords=[])
        self.assertFalse(r.ok)
        self.assertTrue(r.checks["no_repetition"],
                        "check 5 is expected to pass this — that is why 6 exists")
        self.assertFalse(r.checks["not_degenerate"])

    def test_fenced_table_answer_passes_the_degeneracy_check(self) -> None:
        # A df table is legitimately non-linguistic; it sits inside a fence and
        # the fence is excluded before the character mix is measured.
        out = ("Here is the `df -h` output:\n\n```\n"
               "Filesystem      Size  Used Avail Use% Mounted on\n"
               "/dev/mapper/root 982G   64G  868G   7% /\n"
               "|---------------|------|-----|------|----|\n"
               "```\n\nRoot is 7% used, so there is plenty of room.")
        r = assess_coherence(out, prompt_text="df -h please", expected_keywords=[])
        self.assertTrue(r.ok, r.reason)

    def test_degeneracy_primitives_are_public_and_agree(self) -> None:
        # The serving floor consumes these; they must be importable and must
        # report the values the reason string quotes.
        smear = "#" * 60
        self.assertEqual(nonalnum_share(smear), 1.0)
        self.assertLess(compression_ratio(smear), 0.35)
        self.assertIsNotNone(degeneracy_reason(smear))
        prose = ("You are running kernel 6.18.10-igos-10 and the machine has "
                 "been up for three days with nothing unusual in the journal.")
        self.assertIsNone(degeneracy_reason(prose))
        self.assertLess(nonalnum_share(prose), 0.40)

    def test_degeneracy_abstains_below_the_length_floor(self) -> None:
        # "12." is 33% punctuation by character; too short for the signals to
        # carry information, so the check must abstain rather than guess.
        self.assertIsNone(degeneracy_reason("12."))

    def test_reply_with_no_letter_or_digit_is_degenerate_at_any_length(self) -> None:
        self.assertIsNotNone(degeneracy_reason('"'))

    def test_never_byte_compares_backends(self) -> None:
        # Two differently-rounded-but-coherent answers both pass; the checker has
        # no notion of a reference output to diff against.
        a = assess_coherence("The capital is Paris.", prompt_text="capital?",
                             expected_keywords=["Paris"])
        b = assess_coherence("Paris is the capital of France.",
                             prompt_text="capital?", expected_keywords=["Paris"])
        self.assertTrue(a.ok and b.ok)


if __name__ == "__main__":
    unittest.main()
