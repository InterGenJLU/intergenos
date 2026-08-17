# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Asking about a preference is a recall, not a statement of one.

Measured defect: `_PREF_VERB_RE` ("I prefer/like/use/want X") matches inside a
QUESTION exactly as it does inside a statement, and nothing downstream checked
which it was. "which shell do I use again?" was therefore classified as the bare
declarative preference `I use <again?>`, and the assistant offered to remember
the literal word "again?" as the user's shell — while the shell the user had
actually stored a moment earlier went unmentioned.

The route was right and stays right: a question about a stored fact belongs to
the memory path. Only the parse was wrong. These fixtures pin the corrected
split — an interrogative turn classifies as `recall` and is answered from the
store, a declarative turn classifies exactly as it did before — and the sibling
shapes the same leg mis-parsed ("what do I use for editing again?", "remind me
which shell I use again?").
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from intergen.memory import MemoryManager
from intergen.router import ConversationRouter


class RecallQuestionsAreNotStatements(unittest.TestCase):
    """`classify_declarative` on the interrogative/declarative boundary."""

    RECALL_QUESTIONS = [
        "which shell do I use again?",            # the measured turn
        "what do I use for editing again?",       # sibling: longer trailing tail
        "remind me which shell I use again?",     # sibling: imperative-led ask
        "do I prefer dark mode?",                 # sibling: fronted auxiliary
        "what editor do I prefer again?",
    ]

    STATEMENTS = {
        "I use zsh": ("preference", "preference", "zsh"),
        "I prefer dark mode": ("preference", "preference", "dark mode"),
        "my backup drive is /dev/sdb1": ("preference", "backup drive", "/dev/sdb1"),
        "my screen is too bright": ("complaint", "screen", "too bright"),
    }

    def test_a_question_about_a_preference_classifies_as_recall(self) -> None:
        for message in self.RECALL_QUESTIONS:
            with self.subTest(message=message):
                kind, key, value = MemoryManager.classify_declarative(message)
                self.assertEqual(kind, "recall")
                self.assertIsNone(key)
                self.assertIsNone(value)

    def test_the_question_tail_is_never_stored_as_a_value(self) -> None:
        """The defect's signature: the value was the rest of the question."""
        for message in self.RECALL_QUESTIONS:
            with self.subTest(message=message):
                _kind, _key, value = MemoryManager.classify_declarative(message)
                self.assertNotIn("again", (value or ""))

    def test_declarative_classification_is_unchanged(self) -> None:
        for message, expected in self.STATEMENTS.items():
            with self.subTest(message=message):
                self.assertEqual(
                    MemoryManager.classify_declarative(message), expected)

    def test_an_imperative_to_the_assistant_still_abstains(self) -> None:
        self.assertEqual(
            MemoryManager.classify_declarative("I want you to run the script"),
            (None, None, None))


class TheMemoryRouteAnswersARecallQuestion(unittest.TestCase):
    """`_try_memory` end of the same turn: the stored fact is served, and the
    route claiming the turn is still `memory`."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.memory = MemoryManager(db_path=Path(self.tmp) / "memory.db")
        self.r = ConversationRouter.__new__(ConversationRouter)
        self.r._memory = self.memory
        self.r._pending_memory_offer = None

    def test_the_stored_fact_is_the_answer(self) -> None:
        self.memory.store("your preferred shell", "zsh")
        result = self.r._try_memory("which shell do I use again?")

        self.assertTrue(result.handled)
        self.assertEqual(result.source, "memory")
        self.assertIn("zsh", result.text)
        self.assertNotIn("again?", result.text)

    def test_nothing_is_offered_for_storage(self) -> None:
        """The defect's user-visible half: an offer to remember the question."""
        self.memory.store("your preferred shell", "zsh")
        result = self.r._try_memory("which shell do I use again?")

        self.assertNotIn("Want me to remember", result.text)
        self.assertIsNone(self.r._pending_memory_offer)

    def test_an_empty_store_declines_rather_than_answering_vacuously(self) -> None:
        """Nothing stored that the question names -> the route hands the turn on
        instead of composing an answer from a lookup that found nothing."""
        result = self.r._try_memory("which shell do I use again?")

        self.assertFalse(result.handled)

    def test_a_fact_the_question_does_not_name_is_not_served(self) -> None:
        self.memory.store("your backup drive", "/dev/sdb1")
        result = self.r._try_memory("which shell do I use again?")

        self.assertFalse(result.handled)

    def test_a_genuine_statement_still_gets_the_store_offer(self) -> None:
        result = self.r._try_memory("I use zsh")

        self.assertTrue(result.handled)
        self.assertEqual(result.source, "memory")
        self.assertIn("Want me to remember", result.text)
        self.assertIsNotNone(self.r._pending_memory_offer)


if __name__ == "__main__":
    unittest.main()
