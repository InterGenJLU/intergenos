# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A forget takes the facts that carry its subject as a word, and no others.

THE DEFECT, read on the tree in intergen/memory.py, ``delete_by_key``. Each form
of the subject is matched with ``LIKE '%form%'`` against the key and the value.
That is a containment test: it asks whether the subject's letters occur
anywhere, not whether the subject is there as a word. So "forget my car" —
forms "my car", "your car" and "car" — also took "carpet colour", because "car"
is inside "carpet", and the reply counted that fact as one the person asked to
forget: "Done. I've forgotten 2 things about 'my car'."

WHAT THE CODE ITSELF PROMISES. :meth:`MemoryManager.forget_forms` says that a
subject naming nothing the user stored still matches nothing. A person who says
"car" has named the car, not the carpet. The rule is the one the reset parser
was corrected to: a word is not a substring.

THE SAME CLASS IN THE PARSER IN FRONT OF IT. The forget patterns are applied
with ``re.search`` and their verbs carried no word boundary, so the reset
pattern found "clear facts" inside "nuclear facts" and "clear memories" inside
"unclear memories", and its noun found "facts" inside "factsheets": a sentence
asking for nothing reached ``clear_all()`` and every stored fact was marked
deleted. And "undelete the memory about my car" was read as a forget of the car.

THE CELLS. The car/carpet pair, on the key and on the value; a multi-word
subject against a longer phrase that holds it only as a substring; a subject
that names nothing stored, which must match nothing and say so; the parser's
four sentences, and one of them through the router's memory turn. Each is red
before the correction. The controls beside them are green at both ends: the
correction narrows what a forget takes and never stops it taking what it names.

WHAT THIS FILE DOES NOT ASSERT. Nothing about the bytes on disk — the deletion
is soft by design and the storage contract is settled elsewhere. These cases are
about WHICH rows a forget takes, and which sentences are forgets at all.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

from intergen import memory as memory_module
from intergen.interfaces.types import HardwareTierLevel
from intergen.memory import MemoryManager
from intergen.router import ConversationRouter

ALL = "__ALL__"


def _fresh_store() -> MemoryManager:
    """A MemoryManager on its own throwaway store, with nothing else in it."""
    tmp = tempfile.mkdtemp(prefix="forget-words-")
    os.environ["XDG_DATA_HOME"] = os.path.join(tmp, "data")
    os.environ["XDG_STATE_HOME"] = os.path.join(tmp, "state")
    os.makedirs(os.environ["XDG_DATA_HOME"], exist_ok=True)
    os.makedirs(os.environ["XDG_STATE_HOME"], exist_ok=True)
    return MemoryManager()


def _rows(mm: MemoryManager) -> list[tuple[str, str, int]]:
    """Every row in the store, asked of the database rather than of the API."""
    conn = sqlite3.connect(str(mm._db_path))
    try:
        return [(k, v, d) for k, v, d in
                conn.execute("SELECT key, value, deleted FROM facts")]
    finally:
        conn.close()


def _live_keys(mm: MemoryManager) -> list[str]:
    return sorted(k for k, _v, deleted in _rows(mm) if not deleted)


def _store(*facts: tuple[str, str]) -> MemoryManager:
    mm = _fresh_store()
    for key, value in facts:
        mm.store(key, value)
    assert mm.count == len(facts), (
        "control: the facts were not all stored, so anything below would "
        "measure a match against the wrong store (rows: %r)" % (_rows(mm),))
    return mm


def _router(mm: MemoryManager) -> ConversationRouter:
    """The real router's memory turn, with only what that path reads set."""
    r = ConversationRouter.__new__(ConversationRouter)
    r._memory = mm
    r._hardware_tier = HardwareTierLevel.TIER_2
    r._lock_dispatch = False
    return r


# ── the cells: red before the correction ────────────────────────────────────

class TheCarIsNotTheCarpet(unittest.TestCase):
    """A one-word subject against a key and a value that hold it only inside a
    longer word."""

    def test_forget_my_car_takes_the_car_and_leaves_the_carpet(self) -> None:
        mm = _store(("car", "a red hatchback"), ("carpet colour", "grey"))

        removed = mm.delete_by_key("my car")

        self.assertEqual(removed, 1, (
            "the subject 'my car' names one stored fact, yet delete_by_key "
            f"reported removing {removed}: 'car' was matched inside 'carpet'. "
            f"Rows: {_rows(mm)}"))
        self.assertEqual(_live_keys(mm), ["carpet colour"], (
            "a forget took a fact the person did not name"))

    def test_a_value_that_only_contains_the_subject_is_left_alone(self) -> None:
        mm = _store(("car", "a red hatchback"),
                    ("parking spot", "beside the carport"))

        removed = mm.delete_by_key("my car")

        self.assertEqual(removed, 1, (
            f"'car' was matched inside the value 'carport'. Rows: {_rows(mm)}"))
        self.assertEqual(_live_keys(mm), ["parking spot"])

    def test_the_reply_counts_only_what_was_named(self) -> None:
        mm = _store(("car", "a red hatchback"), ("carpet colour", "grey"))

        reply = mm.format_forget_response(
            MemoryManager.is_forget_request("forget my car"))

        self.assertEqual(reply, "Done. I've forgotten 1 thing about 'my car'.", (
            "the reply counted a fact the person did not name as one they "
            "asked to forget"))


class AMultiWordSubjectIsMatchedAsItsWords(unittest.TestCase):
    """A subject of several words names a fact only where all of them stand in
    it as words."""

    def test_forget_my_backup_drive_leaves_the_backup_driver(self) -> None:
        mm = _store(("backup drive", "/dev/sdb1"),
                    ("backup driver version", "2.1"))

        removed = mm.delete_by_key("my backup drive")

        self.assertEqual(removed, 1, (
            "'backup drive' was matched inside 'backup driver version'. "
            f"Rows: {_rows(mm)}"))
        self.assertEqual(_live_keys(mm), ["backup driver version"])


class ASubjectNamingNothingMatchesNothing(unittest.TestCase):
    """The method's own contract, reached the way a person reaches it."""

    def test_only_a_carpet_stored_and_the_car_forgotten_takes_nothing(self) -> None:
        mm = _store(("carpet colour", "grey"))

        reply = mm.format_forget_response(
            MemoryManager.is_forget_request("forget my car"))

        self.assertEqual(_live_keys(mm), ["carpet colour"], (
            f"a forget of a car nobody stored took the carpet; it said {reply!r}"))
        self.assertEqual(reply, "I don't have any memories about 'my car'.", (
            "the subject names nothing that was stored, so the true reply is "
            f"that there is no such memory; the reply was {reply!r}"))


class TheForgetVerbsAreWords(unittest.TestCase):
    """The same class in the parser: a forget verb, and the reset noun, are
    words, not substrings."""

    def test_nuclear_facts_is_not_a_reset(self) -> None:
        self.assertIsNone(
            MemoryManager.is_forget_request("tell me some nuclear facts"),
            "'clear facts' was found inside 'nuclear facts' and the sentence "
            "was read as a request to clear every stored fact")

    def test_unclear_memories_is_not_a_reset(self) -> None:
        self.assertIsNone(
            MemoryManager.is_forget_request("I have unclear memories of that day"),
            "'clear memories' was found inside 'unclear memories'")

    def test_factsheets_are_not_facts(self) -> None:
        self.assertIsNone(
            MemoryManager.is_forget_request("clear factsheets from my notes"),
            "the reset noun 'facts' was found inside 'factsheets'")

    def test_undelete_is_not_delete(self) -> None:
        self.assertIsNone(
            MemoryManager.is_forget_request("undelete the memory about my car"),
            "'delete' was found inside 'undelete', which asks for the opposite")

    def test_the_memory_turn_clears_nothing_for_nuclear_facts(self) -> None:
        mm = _store(("car", "a red hatchback"), ("editor", "vim"))

        result = _router(mm)._try_memory("tell me some nuclear facts")

        self.assertEqual(_live_keys(mm), ["car", "editor"], (
            "the memory turn cleared the person's stored facts for a sentence "
            f"that asked for nothing; it replied {result.text!r}"))


# ── the controls: green at both ends ────────────────────────────────────────

class WhatAWordStillNames(unittest.TestCase):
    """The correction narrows; it must not stop a forget taking what it names."""

    def test_the_word_at_the_start_or_end_of_a_longer_key(self) -> None:
        mm = _store(("car insurance", "renewed in May"),
                    ("rental car", "returned"), ("editor", "vim"))

        removed = mm.delete_by_key("my car")

        self.assertEqual(removed, 2, f"rows: {_rows(mm)}")
        self.assertEqual(_live_keys(mm), ["editor"])

    def test_the_words_inside_a_longer_phrase(self) -> None:
        mm = _store(("old backup drive", "/dev/sdc1"), ("editor", "vim"))

        removed = mm.delete_by_key("my backup drive")

        self.assertEqual(removed, 1, f"rows: {_rows(mm)}")
        self.assertEqual(_live_keys(mm), ["editor"])

    def test_a_word_beside_punctuation(self) -> None:
        mm = _store(("car-park pass", "level 2"), ("editor", "vim"))

        removed = mm.delete_by_key("my car")

        self.assertEqual(removed, 1, f"rows: {_rows(mm)}")
        self.assertEqual(_live_keys(mm), ["editor"])

    def test_case_is_not_part_of_the_word(self) -> None:
        mm = _store(("Car", "a red hatchback"), ("editor", "vim"))

        removed = mm.delete_by_key("my CAR")

        self.assertEqual(removed, 1, f"rows: {_rows(mm)}")
        self.assertEqual(_live_keys(mm), ["editor"])

    def test_a_subject_that_starts_and_ends_on_punctuation(self) -> None:
        """An edge of the subject that is not a word character has no word to
        run on from, so nothing is asked of the text there."""
        mm = _store(("backup drive", "mounted at /dev/sdb1."), ("editor", "vim"))

        removed = mm.delete_by_key("/dev/sdb1.")

        self.assertEqual(removed, 1, f"rows: {_rows(mm)}")
        self.assertEqual(_live_keys(mm), ["editor"])


class TheResetAndTheNamedForgetsStillParse(unittest.TestCase):
    """Every verb still reads where it stands as a word."""

    def test_the_reset_requests(self) -> None:
        for sentence in ("clear all my memories", "clear my facts",
                         "please clear all my memories.",
                         "Clear all my facts!"):
            with self.subTest(sentence=sentence):
                self.assertEqual(MemoryManager.is_forget_request(sentence), ALL)

    def test_the_named_forgets(self) -> None:
        for sentence, subject in (
                ("forget my car", "my car"),
                ("please forget about my car", "my car"),
                ("delete the memory about my car", "my car"),
                ("remove the fact about my car", "my car"),
                ("don't remember my car", "my car")):
            with self.subTest(sentence=sentence):
                self.assertEqual(MemoryManager.is_forget_request(sentence),
                                 subject)

    def test_a_negated_forget_is_still_not_a_forget(self) -> None:
        self.assertIsNone(MemoryManager.is_forget_request("don't forget my car"))


class EveryForgetPatternIsAnchoredAtAWord(unittest.TestCase):
    """Read off the table itself, so a pattern added later without its anchor
    is named here rather than found the way these were."""

    def test_every_verb_starts_at_a_word_boundary(self) -> None:
        patterns = [p.pattern for p in memory_module._FORGET_PATTERNS]
        self.assertTrue(patterns, "control: the table is empty")
        unanchored = [p for p in patterns if not p.startswith(r"\b")]
        self.assertEqual(unanchored, [], (
            "a forget pattern is searched anywhere in the sentence and its verb "
            f"carries no word boundary, so it matches inside a longer word: "
            f"{unanchored}"))

    def test_the_reset_noun_ends_at_a_word_boundary(self) -> None:
        resets = [p for p in memory_module._FORGET_PATTERNS if not p.groups]
        self.assertEqual(len(resets), 1, (
            "control: expected exactly one pattern without a capture, the "
            f"reset; found {[p.pattern for p in resets]}"))
        self.assertTrue(resets[0].pattern.endswith(r"\b"), (
            "the reset pattern's noun carries no closing word boundary, so "
            f"'facts' is found inside 'factsheets': {resets[0].pattern}"))


if __name__ == "__main__":
    unittest.main()
