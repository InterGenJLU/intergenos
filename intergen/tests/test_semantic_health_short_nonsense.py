# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Short, fluent, meaningless replies must be named by an instrument.

THE REGISTERED MISS, pinned as an uncaught boundary when the fast-path screen
landed: asked "Is sshd enabled?", the model answered

    As much least pragmatic unpaid tool

Six real English words, no repetition, no foreign script, no broken bytes, and
nothing whatsoever to do with the question. Every instrument in the serving
floor passed it: the repetition check needs ten words, the degeneracy predicate
needs symbol density, and the corruption screen looks for structural damage. The
same class covers 'plepp' as a package answer and 'T < ( <' as a removal answer.

WHAT MAKES IT CHECKABLE WITHOUT JUDGING MEANING: these are not sentences. A
short reply that carries no verb, no number, no identifier and no terminal
punctuation has no grammatical spine — it is a bag of words, which is a SHAPE
fact, not a semantic one. That keeps the check inside this module's declared
scope (corruption, never correctness) and keeps it deterministic.

CALIBRATED against the four sealed runs of 2026-08-07 and 2026-08-11 (656
replies, 126 of them eight words or shorter): FOUR flagged, every one of them
judge-failed or judge-flagged, ZERO false positives — and all four are named by
no other instrument today, including all three registered misses ('plepp', the
boundary string, and 'T < ( <'). The short good replies in the same corpora —
hostnames, kernel versions, "Nice try, but no.", "What can I help with?" — all
pass, and this file pins the ones that would break first if a threshold moved.

A SCRIPT GUARD keeps the rule honest: everything it reads (word counts, an
English spine list, ASCII terminators) assumes a whitespace-delimited Latin
sentence, so non-Latin output is skipped entirely and left to the flood check,
which already knows when the conversation called for that script. Without the
guard a legitimate Chinese answer to a Chinese question flags on sight — the
existing suite caught exactly that.
"""
from __future__ import annotations

import unittest

from intergen.semantic_health import FLAG_SHORT_NONSENSE, assess_semantic_health


def _flags(text, **kw):
    return assess_semantic_health(text, **kw).flags


class TheRegisteredMissesAreNamedTests(unittest.TestCase):
    def test_the_boundary_string_is_flagged(self):
        self.assertIn(FLAG_SHORT_NONSENSE,
                      _flags("As much least pragmatic unpaid tool"))

    def test_a_one_word_nonsense_answer_is_flagged(self):
        self.assertIn(FLAG_SHORT_NONSENSE, _flags("plepp"))

    def test_a_symbol_fragment_is_flagged(self):
        self.assertIn(FLAG_SHORT_NONSENSE, _flags("T < ( <"))

    def test_a_conjunction_smear_is_flagged(self):
        self.assertIn(FLAG_SHORT_NONSENSE, _flags(", and and,"))


class RealShortAnswersSurviveTests(unittest.TestCase):
    """Every one of these is a real reply from the sealed corpora."""

    CLEAN = (
        "This machine's hostname is intergenos-ge9b-12-dfb-dev.",
        "Your hostname is intergenos-ge9b-12-dfb-dev.",
        "You're running kernel 6.18.10-igos-10.",
        "The Berlin Wall fell in 1989.",
        "That operation is blocked for safety reasons.",
        "Nice try, but no.",
        "What can I help with?",
        "I'm InterGen.",
        "Thanks! I'm glad I could help.",
        "12.",
        "sshd is enabled.",
        "Yes.",
        "No.",
        "Disk usage: 45% used.",
        "See `pkm install htop`.",
    )

    def test_none_of_the_real_short_answers_flag(self):
        for text in self.CLEAN:
            with self.subTest(text=text):
                self.assertNotIn(FLAG_SHORT_NONSENSE, _flags(text))

    def test_a_long_reply_is_out_of_scope_whatever_its_words(self):
        # The check is about SHORT replies; length alone is what makes the
        # missing spine diagnostic. Longer text is the other checks' business.
        long_bag = " ".join(["pragmatic"] * 30)
        self.assertNotIn(FLAG_SHORT_NONSENSE, _flags(long_bag))

    def test_a_legitimate_non_latin_reply_is_left_to_the_flood_check(self):
        # The script guard. Without it, a Chinese answer to a Chinese question
        # flags on sight: no spaces, no English spine, no ASCII terminator.
        self.assertNotIn(
            FLAG_SHORT_NONSENSE,
            _flags("你好！很高兴为你服务。",
                   conversation_texts=["你能帮我做个计划吗？"]))

    def test_an_empty_reply_is_not_this_class(self):
        # Empty is the ladder's own reason and is handled there.
        self.assertNotIn(FLAG_SHORT_NONSENSE, _flags(""))
        self.assertNotIn(FLAG_SHORT_NONSENSE, _flags("   "))

    def test_one_stray_non_latin_letter_does_not_switch_the_check_off(self):
        # Measured by cross-review: appending a single Cyrillic letter to this
        # module's own worked example — 3.2% of its alphabetic characters —
        # took the reply from flagged to NO flags at all, because the guard
        # fired on the first non-Latin character while the check it defers to
        # has its own, much higher threshold. A stray glyph is itself a
        # corruption signature, so the hatch sat on the target population.
        self.assertIn(FLAG_SHORT_NONSENSE,
                      _flags("As much least pragmatic unpaid tool а"))

    def test_the_guard_still_defers_a_predominantly_non_latin_reply(self):
        # The reason the guard exists is unchanged: a reply actually written in
        # another script is the flood check's business, not this one's.
        self.assertNotIn(
            FLAG_SHORT_NONSENSE,
            _flags("你好！很高兴为你服务。",
                   conversation_texts=["你能帮我做个计划吗？"]))


class TheShapeRuleIsExplicitTests(unittest.TestCase):
    """Each escape hatch exists because a real reply needed it."""

    def test_a_verb_makes_it_a_sentence(self):
        self.assertNotIn(FLAG_SHORT_NONSENSE, _flags("The service is active"))

    def test_a_number_carries_data(self):
        self.assertNotIn(FLAG_SHORT_NONSENSE, _flags("979 packages"))

    def test_an_identifier_carries_data(self):
        self.assertNotIn(FLAG_SHORT_NONSENSE, _flags("/etc/fstab"))
        self.assertNotIn(FLAG_SHORT_NONSENSE, _flags("`pkm install htop`"))

    def test_terminal_punctuation_means_a_finished_sentence(self):
        self.assertNotIn(FLAG_SHORT_NONSENSE,
                         _flags("Pragmatic unpaid tool."))

    def test_the_flag_is_reported_with_its_measurements(self):
        res = assess_semantic_health("As much least pragmatic unpaid tool")
        detail = res.detail.get(FLAG_SHORT_NONSENSE)
        self.assertIsInstance(detail, dict)
        self.assertEqual(detail.get("words"), 6)
        self.assertFalse(detail.get("has_verb"))


class ItStaysInsideTheModuleTests(unittest.TestCase):
    def test_the_flag_is_part_of_the_result_contract(self):
        # One instrument, extended — not a second detector living elsewhere.
        res = assess_semantic_health("plepp")
        self.assertIn(FLAG_SHORT_NONSENSE, res.flags)
        self.assertFalse(res.ok)


if __name__ == "__main__":
    unittest.main()
