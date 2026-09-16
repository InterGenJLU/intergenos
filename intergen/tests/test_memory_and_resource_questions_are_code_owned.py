# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Two questions a person asks in their first five minutes, answered in code.

Both were WRONG ANSWERS on the shipped web surface, not failed turns.

(1) "What was my first question to you?" — an ORDINAL question about the
    conversation itself. The model-facing buffer is trimmed in place to the
    last twenty messages, so the opening exchange is destroyed once the
    conversation is ten turns old, and the assistant answered with a LATER
    turn. The relevance index holds the verbatim exchanges, but it is queried
    by SEMANTIC similarity and only ever holds a turn the embedder managed to
    embed — so an ordinal question has no path that reads the first turn, and
    on a degraded embedder it would have none at all. The fix keeps an
    unbounded, verbatim, in-order transcript on the conversation and answers
    the ordinal class from it, in code, with no model and no embedder.

(2) "And memory?" after "How much free disk space do I have?" — an elliptical
    follow-up. The disk question reaches a code-owned probe and is answered in
    milliseconds. The memory question reaches nothing: the state cache holds
    `free -h`, but its value is multi-line and the cache-served route declines
    multi-line output, so the turn fell to the model, which does not have the
    numbers. The fix gives memory the same code-owned probe disk already has,
    and resolves the bare elliptical form only when the turn before it was a
    resource-state question — never on its own.

Both answers are composed in code from real output. Neither can fabricate: a
turn that is not in the transcript, and a probe that returns nothing, DECLINE.
"""

from __future__ import annotations

import time
import unittest

from intergen.interfaces.types import MessageRole
from intergen.router import ConversationRouter


def _router(fixed_output: str | None = None, *, embedder=None):
    """A router with the collaborators these two paths touch, and nothing else."""
    r = ConversationRouter.__new__(ConversationRouter)
    r._max_history = 20
    r._record = lambda *a, **k: None
    r._won = lambda *a, **k: None
    r._current_query_type = "general"
    r._memory = None
    r._embedder = embedder
    r._state_cache = None
    r._tools = None
    r._review_callback = None
    r._hardware_tier = "locked"
    # The probe's dispatch is stubbed at the one seam the D1 table uses, so the
    # test drives the ROUTING decision and the rendering, not run_command.
    r._run_fixed_command = lambda command: fixed_output
    r.detach_conversation()
    return r


FREE_H = (
    "               total        used        free      shared  buff/cache   available\n"
    "Mem:            15Gi       5.2Gi       1.1Gi       412Mi       9.4Gi       9.3Gi\n"
    "Swap:          8.0Gi          0B       8.0Gi\n"
)


class OrdinalConversationQuestionTests(unittest.TestCase):
    """(1) The ordinal class reads the transcript, not the trimmed window."""

    def _drive(self, r, conv, pairs):
        with r.bind_conversation(conv):
            for user, answer in pairs:
                r._append_history(user, answer)

    def test_the_first_question_survives_the_window_trim(self):
        r = _router()
        conv = r.new_conversation()
        first = "How much free disk space do I have?"
        self._drive(r, conv, [(first, "You have 300G free.")]
                    + [(f"question {n}", f"answer {n}") for n in range(2, 26)])
        # The model-facing buffer has been trimmed past the opening exchange.
        # (It trims to the last _max_history messages once it exceeds twice
        # that, so it holds far fewer than the 50 messages written here.)
        self.assertLess(len(conv.history), 2 * 25)
        self.assertNotIn(first, [m.content for m in conv.history])
        # The verbatim transcript still holds it, in order.
        self.assertEqual(conv.transcript[0][0], first)
        with r.bind_conversation(conv):
            result = r._try_direct_answer(
                "What was my first question to you?",
                "What was my first question to you?", time.monotonic())
        self.assertIsNotNone(result)
        self.assertEqual(result.source, "direct_answer")
        self.assertIn(first, result.text)
        self.assertEqual(result.answer_linkage.kind, "code")

    def test_the_answer_needs_no_embedder(self):
        r = _router(embedder=None)
        conv = r.new_conversation()
        self.assertIsNone(conv.turn_index)
        first = "Who are you?"
        self._drive(r, conv, [(first, "I am InterGen."), ("and after that?", "…")])
        with r.bind_conversation(conv):
            result = r._try_direct_answer("what was my first question",
                                          "what was my first question",
                                          time.monotonic())
        self.assertIsNotNone(result)
        self.assertIn(first, result.text)

    def test_a_later_ordinal_reads_that_turn(self):
        r = _router()
        for ask, expected in (("what was my second question", "two"),
                              ("what was the last thing I asked you", "three")):
            conv = r.new_conversation()
            self._drive(r, conv, [("one", "a"), ("two", "b"), ("three", "c")])
            with r.bind_conversation(conv):
                result = r._try_direct_answer(ask, ask, time.monotonic())
            self.assertIn(expected, result.text)

    def test_an_ordinal_turn_is_itself_part_of_the_conversation(self):
        """Asked twice, the second answer names the first ASK — because that is
        what the person actually said last. The transcript is the record of the
        conversation, not a filtered view of it: hiding a turn from it to make
        the answer read better would be the assistant deciding what the person
        did and did not say."""
        r = _router()
        conv = r.new_conversation()
        self._drive(r, conv, [("one", "a"), ("two", "b")])
        with r.bind_conversation(conv):
            r._try_direct_answer("what was my first question",
                                 "what was my first question", time.monotonic())
            last = r._try_direct_answer("what was the last thing I asked you",
                                        "what was the last thing I asked you",
                                        time.monotonic())
        self.assertIn("what was my first question", last.text)

    def test_an_ordinal_with_no_such_turn_declines_rather_than_inventing_one(self):
        r = _router()
        conv = r.new_conversation()
        with r.bind_conversation(conv):
            # Nothing has been said yet.
            self.assertIsNone(r._try_direct_answer(
                "What was my first question to you?",
                "What was my first question to you?", time.monotonic()))
        self._drive(r, conv, [("one", "a")])
        with r.bind_conversation(conv):
            # One turn exists; there is no third.
            self.assertIsNone(r._try_direct_answer(
                "what was my third question", "what was my third question",
                time.monotonic()))

    def test_the_ordinal_answer_is_recorded_like_any_other_turn(self):
        r = _router()
        conv = r.new_conversation()
        self._drive(r, conv, [("one", "a")])
        with r.bind_conversation(conv):
            result = r._try_direct_answer("what was my first question",
                                          "what was my first question",
                                          time.monotonic())
        self.assertEqual(
            [(m.role, m.content) for m in conv.history][-2:],
            [(MessageRole.USER, "what was my first question"),
             (MessageRole.ASSISTANT, result.text)])

    def test_a_first_question_that_is_not_about_this_conversation_declines(self):
        """"What was the first question on the exam" is not a question about
        this conversation. The possessive is what makes it one."""
        r = _router()
        conv = r.new_conversation()
        self._drive(r, conv, [("one", "a"), ("two", "b")])
        with r.bind_conversation(conv):
            for ask in ("what was the first question on the exam",
                        "what was the first question in the survey"):
                self.assertIsNone(r._try_direct_answer(ask, ask,
                                                       time.monotonic()), ask)

    def test_clearing_a_conversation_clears_its_transcript(self):
        r = _router()
        conv = r.new_conversation()
        self._drive(r, conv, [("one", "a")])
        conv.clear()
        self.assertEqual(conv.transcript, [])


class MemoryResourceQuestionTests(unittest.TestCase):
    """(2) Memory gets the code-owned probe disk already had."""

    def test_a_plain_memory_ask_is_answered_from_the_probe(self):
        r = _router(FREE_H)
        conv = r.new_conversation()
        with r.bind_conversation(conv):
            result = r._try_direct_answer("how much free memory do I have?",
                                          "how much free memory do I have?",
                                          time.monotonic())
        self.assertIsNotNone(result)
        self.assertEqual(result.source, "direct_answer")
        self.assertIn("9.3Gi", result.text)
        self.assertIn("15Gi", result.text)
        self.assertEqual(result.answer_linkage.kind, "dispatch")
        self.assertEqual(result.answer_linkage.tool, "run_command")

    def test_the_ram_wording_reaches_the_same_probe(self):
        r = _router(FREE_H)
        conv = r.new_conversation()
        with r.bind_conversation(conv):
            result = r._try_direct_answer("how much RAM is free",
                                          "how much RAM is free",
                                          time.monotonic())
        self.assertIsNotNone(result)
        self.assertIn("9.3Gi", result.text)

    def test_the_elliptical_follow_up_resolves_against_a_resource_question(self):
        r = _router(FREE_H)
        conv = r.new_conversation()
        with r.bind_conversation(conv):
            r._append_history("How much free disk space do I have?",
                              "You have 300G free on the root filesystem.")
            result = r._try_direct_answer("And memory?", "And memory?",
                                          time.monotonic())
        self.assertIsNotNone(result)
        self.assertEqual(result.source, "direct_answer")
        self.assertIn("9.3Gi", result.text)

    def test_the_bare_elliptical_declines_without_a_resource_antecedent(self):
        r = _router(FREE_H)
        conv = r.new_conversation()
        with r.bind_conversation(conv):
            r._append_history("What year was Linux first released?", "1991.")
            self.assertIsNone(r._try_direct_answer("And memory?", "And memory?",
                                                   time.monotonic()))
        conv2 = r.new_conversation()
        with r.bind_conversation(conv2):
            # Nothing before it at all.
            self.assertIsNone(r._try_direct_answer("And memory?", "And memory?",
                                                   time.monotonic()))

    def test_a_question_about_the_conversation_is_not_stolen_by_the_probe(self):
        r = _router(FREE_H)
        conv = r.new_conversation()
        with r.bind_conversation(conv):
            r._append_history("one", "a")
            for ask in ("do you remember what I asked you?",
                        "what was my first question to you?"):
                result = r._try_direct_answer(ask, ask, time.monotonic())
                if result is not None:
                    self.assertNotIn("9.3Gi", result.text)

    def test_a_teaching_ask_about_memory_is_left_to_the_explain_gate(self):
        r = _router(FREE_H)
        conv = r.new_conversation()
        with r.bind_conversation(conv):
            for ask in ("how do I check my memory usage",
                        "what command shows free memory"):
                self.assertIsNone(r._try_direct_answer(ask, ask,
                                                       time.monotonic()))

    def test_a_memory_ask_about_one_program_is_not_answered_machine_wide(self):
        """`free -h` does not know what firefox is using. Answering a scoped
        question with a system-wide figure is a wrong answer, which is the
        defect this whole module is about."""
        r = _router(FREE_H)
        conv = r.new_conversation()
        with r.bind_conversation(conv):
            for ask in ("how much memory is used by firefox",
                        "which process is using the most memory",
                        "how much RAM is used per container",
                        "what is using my memory"):
                self.assertIsNone(r._try_direct_answer(ask, ask,
                                                       time.monotonic()), ask)

    def test_a_probe_that_returns_nothing_declines(self):
        r = _router(None)
        conv = r.new_conversation()
        with r.bind_conversation(conv):
            self.assertIsNone(r._try_direct_answer("how much free memory do I have?",
                                                   "how much free memory do I have?",
                                                   time.monotonic()))


if __name__ == "__main__":
    unittest.main()
