# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""One stated fact is stored once, under one key.

THE DEFECT, measured on the shipped tree. A user says

    remember that my backup drive is /dev/sdb1

and InterGen replies

    Got it. I'll remember: **your backup drive** = /dev/sdb1, **backup drive** = /dev/sdb1

Two stored entries for one stated fact. The same doubling comes back out again
on every surface that reads the store: the recall answer says "Backup drive is
/dev/sdb1. Your backup drive is /dev/sdb1.", and the transparency list says
"I remember 2 things about you" and then names the one thing twice. That last
pair is in this repository already, recorded verbatim in a graded run at
docs/research/ai_integration/baseline_results/round19_results.json.

WHY IT HAPPENS. ``MemoryManager.extract_and_store`` walks its pattern table and
stores EVERY pattern that matched, as though each were a separate fact. The
patterns are not separate facts — they are alternative readings of one sentence.
"remember that my backup drive is /dev/sdb1" is claimed by the "remember that X
is Y" pattern (key "my backup drive", perspective-shifted on the way in to
"your backup drive") and again by the "my X is Y" pattern (key "backup drive"),
so one sentence writes two rows.

THE SAME CAUSE ALSO STORES A WRONG VALUE. "remember that the server is at
/srv/data" is read by the general pattern as key "the server" with the value
"at /srv/data" — the preposition swallowed into the value — and by the
system-location pattern as key "server" with the value "/srv/data". Both rows
are kept, so the store holds one right answer and one wrong one for the same
question.

WHAT THIS FILE PINS. One stated fact makes one row; the key is the subject the
user named with the possessive stripped, which is the form the recall matcher
and the forget path both already work in; where two readings of one sentence
disagree about the value, the more specific reading's value is what is stored;
and a store written by an earlier release, which already holds the twin rows,
folds to one row per stated fact the next time it is opened, so a machine that
has been running since R001.1 stops answering twice as well.

WHAT IT DOES NOT PIN. A sentence that genuinely states two facts ("remember
that my editor is vim and my shell is zsh") is still parsed as one fact with a
run-on value. That is a defect of the pattern table, not of the one-row rule,
and it is untouched here.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from intergen.memory import MemoryManager
from intergen.router import ConversationRouter

_DRIVE = "/dev/sdb1"


def _store() -> MemoryManager:
    """A MemoryManager on its own throwaway database."""
    return MemoryManager(db_path=Path(tempfile.mkdtemp(prefix="onekey-"))
                         / "memory.db")


def _active_rows(mm: MemoryManager) -> list[tuple[str, str]]:
    """Every active row, asked of the database rather than of the API."""
    conn = sqlite3.connect(str(mm._db_path))
    try:
        return [(k, v) for k, v in conn.execute(
            "SELECT key, value FROM facts WHERE deleted = 0 "
            "ORDER BY created_at")]
    finally:
        conn.close()


class OneSentenceMakesOneRow(unittest.TestCase):
    """The store side: what the extractor writes for one stated fact."""

    def test_the_measured_sentence_stores_one_fact(self) -> None:
        mm = _store()
        facts = mm.extract_and_store(
            f"remember that my backup drive is {_DRIVE}")
        self.assertEqual(
            [(f.key, f.value) for f in facts], [("backup drive", _DRIVE)],
            "one stated fact was stored as more than one fact")

    def test_the_key_is_the_subject_with_the_possessive_stripped(self) -> None:
        mm = _store()
        mm.extract_and_store(f"remember that my backup drive is {_DRIVE}")
        self.assertEqual(_active_rows(mm), [("backup drive", _DRIVE)])

    def test_the_second_measured_sentence_behaves_the_same(self) -> None:
        """The battery recorded this pair, not only the drive one."""
        mm = _store()
        mm.extract_and_store("remember that my default editor is neovim")
        self.assertEqual(_active_rows(mm), [("default editor", "neovim")])

    def test_the_bare_statement_and_the_remember_form_agree(self) -> None:
        """"my X is Y" and "remember that my X is Y" must land on one key, or
        the same fact stated twice makes two rows across two turns."""
        first = _store()
        first.extract_and_store(f"my backup drive is {_DRIVE}")
        second = _store()
        second.extract_and_store(f"remember that my backup drive is {_DRIVE}")
        self.assertEqual([k for k, _v in _active_rows(first)],
                         [k for k, _v in _active_rows(second)])

    def test_the_more_specific_reading_supplies_the_value(self) -> None:
        """Where two readings of one sentence disagree, the stray preposition
        does not survive into the stored value."""
        mm = _store()
        mm.extract_and_store("remember that the server is at /srv/data")
        self.assertEqual(_active_rows(mm), [("server", "/srv/data")])

    def test_two_different_facts_still_make_two_rows(self) -> None:
        """The control: collapsing readings of ONE sentence must not collapse
        two sentences that state different things."""
        mm = _store()
        mm.extract_and_store(f"remember that my backup drive is {_DRIVE}")
        mm.extract_and_store("remember that my editor is vim")
        self.assertEqual(sorted(_active_rows(mm)),
                         [("backup drive", _DRIVE), ("editor", "vim")])

    def test_restating_the_same_fact_updates_the_one_row(self) -> None:
        mm = _store()
        mm.extract_and_store(f"remember that my backup drive is {_DRIVE}")
        mm.extract_and_store("remember that my backup drive is /dev/sdc1")
        self.assertEqual(_active_rows(mm), [("backup drive", "/dev/sdc1")])

    def test_a_preference_is_unaffected(self) -> None:
        """Control: a pattern with a fixed key keeps it."""
        mm = _store()
        mm.extract_and_store("I prefer dark mode")
        self.assertEqual(_active_rows(mm), [("preference", "dark mode")])

    def test_remember_x_as_y_is_unaffected_but_for_the_possessive(self) -> None:
        mm = _store()
        mm.extract_and_store("remember my ssh key as id_ed25519")
        self.assertEqual(_active_rows(mm), [("ssh key", "id_ed25519")])


class TheReplyNamesItOnce(unittest.TestCase):
    """The surfaces a person actually reads."""

    def setUp(self) -> None:
        self.memory = _store()
        self.r = ConversationRouter.__new__(ConversationRouter)
        self.r._memory = self.memory

    def test_the_acknowledgement_names_one_thing(self) -> None:
        result = self.r._try_memory(
            f"remember that my backup drive is {_DRIVE}")
        self.assertTrue(result.handled)
        self.assertEqual(result.text.count(_DRIVE), 1, result.text)
        self.assertEqual(result.text, f"Got it. I'll remember: "
                                      f"**backup drive** = {_DRIVE}")

    def test_the_recall_answer_says_it_once(self) -> None:
        self.r._try_memory(f"remember that my backup drive is {_DRIVE}")
        answer = self.r._answer_from_stored_facts("what's my backup drive?")
        self.assertIsNotNone(answer)
        self.assertEqual(answer.count(_DRIVE), 1, answer)

    def test_the_transparency_list_counts_one_thing(self) -> None:
        self.r._try_memory(f"remember that my backup drive is {_DRIVE}")
        listing = self.memory.format_transparency_response()
        self.assertIn("I remember 1 thing about you", listing)
        self.assertEqual(listing.count(_DRIVE), 1, listing)


class AStoreWrittenByAnEarlierReleaseFolds(unittest.TestCase):
    """A machine that has been running since R001.1 already holds the twins.
    Fixing the extractor alone leaves that person still being answered twice,
    so the twins are folded when the store is opened."""

    @staticmethod
    def _seed_twins(db_path: Path) -> None:
        """Write the two rows exactly as the earlier release wrote them."""
        mm = MemoryManager(db_path=db_path)
        mm.store("your backup drive", _DRIVE)
        mm.store("backup drive", _DRIVE)

    def test_the_twins_fold_to_one_active_row_on_open(self) -> None:
        db = Path(tempfile.mkdtemp(prefix="onekey-fold-")) / "memory.db"
        self._seed_twins(db)
        reopened = MemoryManager(db_path=db)
        self.assertEqual(_active_rows(reopened), [("backup drive", _DRIVE)])

    def test_the_survivor_is_the_form_the_fix_now_writes(self) -> None:
        db = Path(tempfile.mkdtemp(prefix="onekey-fold-")) / "memory.db"
        self._seed_twins(db)
        reopened = MemoryManager(db_path=db)
        self.assertEqual(reopened.get("backup drive"), _DRIVE)
        self.assertIsNone(reopened.get("your backup drive"))

    def test_the_folded_row_is_soft_deleted_not_dropped(self) -> None:
        """Removing a person's stored data is reversible here, as everywhere
        else in this store."""
        db = Path(tempfile.mkdtemp(prefix="onekey-fold-")) / "memory.db"
        self._seed_twins(db)
        MemoryManager(db_path=db)
        conn = sqlite3.connect(str(db))
        try:
            rows = list(conn.execute("SELECT key, deleted FROM facts"))
        finally:
            conn.close()
        self.assertEqual(sorted(rows), [("backup drive", 0),
                                        ("your backup drive", 1)])

    def test_two_facts_that_merely_share_a_value_are_both_kept(self) -> None:
        """The control on the fold. Twins are the same subject stated once —
        not any two rows that happen to name the same thing."""
        db = Path(tempfile.mkdtemp(prefix="onekey-fold-")) / "memory.db"
        mm = MemoryManager(db_path=db)
        mm.store("backup drive", _DRIVE)
        mm.store("spare drive", _DRIVE)
        reopened = MemoryManager(db_path=db)
        self.assertEqual(sorted(_active_rows(reopened)),
                         [("backup drive", _DRIVE), ("spare drive", _DRIVE)])

    def test_twins_that_disagree_keep_the_later_statement(self) -> None:
        """Twin keys carrying different values are one sentence parsed two
        ways, not a person changing their mind — the shipped extractor wrote
        both rows of an ordinary restatement with the same value. The later
        write is the current answer."""
        db = Path(tempfile.mkdtemp(prefix="onekey-fold-")) / "memory.db"
        mm = MemoryManager(db_path=db)
        mm.store("your backup drive", _DRIVE)
        mm.store("backup drive", "/dev/sdc1")
        reopened = MemoryManager(db_path=db)
        self.assertEqual(_active_rows(reopened), [("backup drive", "/dev/sdc1")])

    def test_a_later_statement_under_the_possessive_key_wins_too(self) -> None:
        """The case the keep-the-canonical-key rule alone would get wrong.
        "remember my backup drive as X" writes only the possessive key, so the
        canonically-keyed row can be the stale one."""
        db = Path(tempfile.mkdtemp(prefix="onekey-fold-")) / "memory.db"
        mm = MemoryManager(db_path=db)
        mm.store("backup drive", _DRIVE)
        mm.store("your backup drive", "/dev/sdc1")
        reopened = MemoryManager(db_path=db)
        self.assertEqual([v for _k, v in _active_rows(reopened)],
                         ["/dev/sdc1"])

    def test_the_swallowed_preposition_row_does_not_survive(self) -> None:
        """The store an upgraded machine actually holds: one sentence read by
        two patterns that disagreed about the value, so the store held a right
        answer and a wrong one to the same question."""
        db = Path(tempfile.mkdtemp(prefix="onekey-fold-")) / "memory.db"
        mm = MemoryManager(db_path=db)
        mm.store("the server", "at /srv/data")
        mm.store("server", "/srv/data")
        reopened = MemoryManager(db_path=db)
        self.assertEqual(_active_rows(reopened), [("server", "/srv/data")])

    def test_rows_the_fold_cannot_name_are_left_alone(self) -> None:
        """A key that is nothing but a determiner reduces to the empty string.
        Every such row would group with every other one, so they are skipped."""
        db = Path(tempfile.mkdtemp(prefix="onekey-fold-")) / "memory.db"
        mm = MemoryManager(db_path=db)
        mm.store("my", "one")
        mm.store("the", "two")
        reopened = MemoryManager(db_path=db)
        self.assertEqual(sorted(_active_rows(reopened)),
                         [("my", "one"), ("the", "two")])

    def test_a_store_with_no_twins_is_untouched(self) -> None:
        db = Path(tempfile.mkdtemp(prefix="onekey-fold-")) / "memory.db"
        mm = MemoryManager(db_path=db)
        mm.store("backup drive", _DRIVE)
        mm.store("editor", "vim")
        reopened = MemoryManager(db_path=db)
        self.assertEqual(sorted(_active_rows(reopened)),
                         [("backup drive", _DRIVE), ("editor", "vim")])


class TheKeyHasOneSpelling(unittest.TestCase):
    """Both writers into the fact store name a key the same way, or the two of
    them make twins across two turns instead of within one."""

    def test_the_offer_acceptance_writes_the_same_key(self) -> None:
        from intergen.conversation_state import ConversationState

        memory = _store()
        r = ConversationRouter.__new__(ConversationRouter)
        r._memory = memory
        r._bound_conversation = ConversationState()
        r._conv.pending_memory_offer = (
            "preference", "backup drive", _DRIVE,
            f"my backup drive is {_DRIVE}")
        r._try_memory("yes")

        memory.extract_and_store(f"remember that my backup drive is {_DRIVE}")
        self.assertEqual(_active_rows(memory), [("backup drive", _DRIVE)])

    def test_the_key_helper_is_idempotent(self) -> None:
        from intergen.memory import fact_key

        for subject in ("my backup drive", "your backup drive", "backup drive",
                        "the server", "preference"):
            with self.subTest(subject=subject):
                once = fact_key(subject)
                self.assertEqual(fact_key(once), once)

    def test_the_key_helper_never_empties_a_real_subject(self) -> None:
        from intergen.memory import fact_key

        self.assertEqual(fact_key("my backup drive"), "backup drive")
        self.assertEqual(fact_key("the server"), "server")
        self.assertEqual(fact_key("preference"), "preference")
        self.assertEqual(fact_key("  my   backup   drive  "), "backup drive")


if __name__ == "__main__":
    unittest.main()
