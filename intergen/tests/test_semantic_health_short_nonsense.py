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

A TERSE ANSWER TO THE QUESTION ASKED is not a word-bag. Measured on a machine's
own model server through the daemon's path: "Paris" answering "What is the
capital of France?", "William Shakespeare" answering "Who wrote Hamlet?" and
four more were flagged, and the person was shown the incoherence fallback in
place of a correct answer. The question supplies the sentence the reply leaves
out, so a reply of the shape the question asks for passes; the registered
misses stay flagged with their own questions, and a greeting answers nothing
whatever it follows. Cells marked CONTROL hold before that escape and after it;
every other cell in that class fails before it.
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


class ATerseAnswerToTheQuestionAskedIsNotAWordBagTests(unittest.TestCase):
    """The question is the fifth escape: a reply of the shape it asks for."""

    # Each pair is a question and this machine's model's own whole answer to it,
    # measured through the daemon's path, each one flagged before the escape.
    MEASURED = (
        ("What is the capital of France?", "Paris"),
        ("Who wrote Hamlet?", "William Shakespeare"),
        ("Which planet is the largest?", "Jupiter"),
        ("What colour is the sky on a clear day?", "Blue"),
        ("How many continents are there?", "Seven"),
        ("Is Paris the capital of France?", "Yes"),
    )

    def test_the_measured_case_paris_is_not_flagged(self):
        self.assertNotIn(
            FLAG_SHORT_NONSENSE,
            _flags("Paris", conversation_texts=["What is the capital of France?"]))

    def test_every_measured_terse_answer_is_not_flagged(self):
        for question, answer in self.MEASURED:
            with self.subTest(question=question, answer=answer):
                self.assertNotIn(FLAG_SHORT_NONSENSE,
                                 _flags(answer, conversation_texts=[question]))

    def test_a_question_typed_without_its_question_mark_is_still_a_question(self):
        self.assertNotIn(
            FLAG_SHORT_NONSENSE,
            _flags("Paris", conversation_texts=["what is the capital of france"]))

    def test_the_question_is_the_last_turn_and_an_earlier_one_is_not_read(self):
        # The turn the reply answers is the last one handed over; it is a
        # question here, after an earlier turn that was not.
        self.assertNotIn(
            FLAG_SHORT_NONSENSE,
            _flags("Jupiter", conversation_texts=["Thanks for that.",
                                                  "Which planet is the largest?"]))

    # Each shape the rule reads a question by, one cell each.

    def test_a_question_word_inside_a_question_is_read(self):
        self.assertNotIn(
            FLAG_SHORT_NONSENSE,
            _flags("William Shakespeare",
                   conversation_texts=["Can you tell me who wrote Hamlet?"]))

    def test_a_yes_no_question_is_read_from_its_question_mark(self):
        self.assertNotIn(FLAG_SHORT_NONSENSE,
                         _flags("Yes", conversation_texts=["sshd enabled?"]))

    def test_a_yes_no_question_is_read_from_its_auxiliary_without_a_mark(self):
        self.assertNotIn(
            FLAG_SHORT_NONSENSE,
            _flags("Yes", conversation_texts=["is paris the capital of france"]))

    def test_a_typographic_apostrophe_in_the_question_word_is_read(self):
        question = "What" + chr(0x2019) + "s the capital of France?"
        self.assertNotIn(FLAG_SHORT_NONSENSE,
                         _flags("Paris", conversation_texts=[question]))

    def test_a_comma_between_the_words_of_an_answer_is_read(self):
        self.assertNotIn(
            FLAG_SHORT_NONSENSE,
            _flags("Paris, France",
                   conversation_texts=["Where is the Eiffel Tower?"]))

    def test_the_reading_is_reported_with_the_flag_detail(self):
        res = assess_semantic_health(
            "Paris", conversation_texts=["What is the capital of France?"])
        detail = res.detail.get(FLAG_SHORT_NONSENSE)
        self.assertEqual(detail.get("question"), "thing")
        self.assertTrue(detail.get("answers_question"))

    # ── CONTROLS: flagged before the escape and after it ──

    def test_control_a_greeting_answering_a_question_is_still_flagged(self):
        for greeting in ("Hello", "Hi"):
            with self.subTest(greeting=greeting):
                self.assertIn(
                    FLAG_SHORT_NONSENSE,
                    _flags(greeting,
                           conversation_texts=["What is the capital of France?"]))

    def test_control_the_probe_greetings_with_no_question_are_still_flagged(self):
        # The 2026-08-26 readiness-probe replies: no turn, no question.
        for greeting in ("Hello", "Hi"):
            with self.subTest(greeting=greeting):
                self.assertIn(FLAG_SHORT_NONSENSE, _flags(greeting))
                self.assertIn(FLAG_SHORT_NONSENSE,
                              _flags(greeting, conversation_texts=[]))

    def test_control_the_registered_misses_with_their_own_questions_stay_flagged(self):
        # "plepp" answered "Is there a package for the GIMP image editor?" in the
        # sealed run; the six-word bag answered "Is sshd enabled?". A yes-or-no
        # question asks for a yes or a no, and neither reply gives one.
        for question, answer in (
                ("Is there a package for the GIMP image editor?", "plepp"),
                ("Is sshd enabled?", "As much least pragmatic unpaid tool")):
            with self.subTest(question=question, answer=answer):
                self.assertIn(FLAG_SHORT_NONSENSE,
                              _flags(answer, conversation_texts=[question]))

    def test_control_a_reply_to_a_request_that_is_not_a_question_stays_flagged(self):
        self.assertIn(FLAG_SHORT_NONSENSE,
                      _flags("T < ( <", conversation_texts=["Remove htop."]))
        self.assertIn(FLAG_SHORT_NONSENSE,
                      _flags("Paris", conversation_texts=["Remove htop."]))

    def test_control_the_six_word_bag_stays_flagged_after_any_question(self):
        # Five words is the longest terse answer; the measured bag is six.
        self.assertIn(
            FLAG_SHORT_NONSENSE,
            _flags("As much least pragmatic unpaid tool",
                   conversation_texts=["What tool should I use?"]))

    def test_control_an_earlier_question_does_not_cover_a_later_turn(self):
        self.assertIn(
            FLAG_SHORT_NONSENSE,
            _flags("Paris", conversation_texts=["What is the capital of France?",
                                                "Remove htop."]))

    def test_control_a_broken_byte_inside_a_terse_answer_is_still_flagged(self):
        self.assertIn(
            FLAG_SHORT_NONSENSE,
            _flags("P" + chr(0xFFFD) + "aris",
                   conversation_texts=["What is the capital of France?"]))


class ItStaysInsideTheModuleTests(unittest.TestCase):
    def test_the_flag_is_part_of_the_result_contract(self):
        # One instrument, extended — not a second detector living elsewhere.
        res = assess_semantic_health("plepp")
        self.assertIn(FLAG_SHORT_NONSENSE, res.flags)
        self.assertFalse(res.ok)


if __name__ == "__main__":
    unittest.main()
