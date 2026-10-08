# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""What the user types is matched as text everywhere the fact store matches it.

WHY A SECOND MODULE. ``test_zz192_p4_04.py`` pins the finding as the verifier
raised it: ``delete_by_key`` put the forget subject into a LIKE pattern with its
``%`` and ``_`` live, so "forget _" emptied the store and "project_notes" also
took "projectXnotes". It is the right case and it stops at that method. Four
things this defect owns are outside it.

WHAT IS MEASURED HERE.

  * THE SAME CLASS AT THE OTHER SITE. ``search`` built its pattern the same way,
    from the same kind of input, so a query of ``_`` found every fact. It has no
    caller in the shipped paths today, which is why it is fixed here rather than
    left for the first caller to find.

  * THE CONTROLS ON THE FIX. Escaping must make a metacharacter mean itself, not
    make it unmatchable: a subject that really holds ``%``, ``_`` or the escape
    character still forgets the fact that holds it. These pass before the
    correction and after it, and are named as the guards they are, not counted
    as reds.

  * THE TURN THE USER ACTUALLY TAKES. The router's memory turn — the code the
    daemon runs when a sentence is a forget request — given "forget _" with two
    facts stored: what it replies, and what is left in the database file.

  * THAT EVERY LIKE GIVEN THE USER'S WORDS DECLARES ITS ESCAPE, read off the
    module's syntax tree. This is the shape of the defect rather than one of its
    inputs, and it is the part a later edit is most likely to reintroduce.
"""
from __future__ import annotations

import ast
import os
import re
import sqlite3
import tempfile
import unittest

import intergen.memory as memory_module
from intergen.interfaces.types import HardwareTierLevel
from intergen.memory import MemoryManager
from intergen.router import ConversationRouter


def _fresh_store() -> MemoryManager:
    """A MemoryManager on its own throwaway store, with nothing else in it.

    The same shape the verifier's own case uses, so both modules measure a store
    that cannot be this machine's.
    """
    tmp = tempfile.mkdtemp(prefix="forget-as-text-")
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


def _two_unrelated_facts() -> MemoryManager:
    """Two facts sharing no substring, neither holding a metacharacter."""
    mm = _fresh_store()
    mm.store("editor", "vim")
    mm.store("backup drive", "/dev/sdb1")
    assert mm.count == 2, (
        "control: the two facts were not stored, so anything below would "
        "measure a match against an empty store (rows: %r)" % (_rows(mm),))
    return mm


def _router(mm: MemoryManager) -> ConversationRouter:
    """The real router's memory turn, with only what that path reads set.

    ``_try_memory`` is reached with no model, no tools and no matcher, and it
    reads neither the hardware tier nor the dispatch lock, so the turn is
    decided by the same code the daemon runs without loading the machinery a
    full construction would.
    """
    r = ConversationRouter.__new__(ConversationRouter)
    r._memory = mm
    r._hardware_tier = HardwareTierLevel.TIER_2
    r._lock_dispatch = False
    return r


# ── the same class at the other site ────────────────────────────────────────

class TheSearchMatchesItsQueryAsTextTests(unittest.TestCase):
    """``search`` took its query as a pattern exactly as the forget did."""

    def test_a_search_for_a_wildcard_finds_nothing_it_did_not_name(self) -> None:
        for query in ("_", "%", "%%", "_%_"):
            with self.subTest(query=query):
                mm = _two_unrelated_facts()
                found = [f.key for f in mm.search(query)]
                self.assertEqual(found, [], (
                    f"a search for {query!r} names no stored fact, yet it "
                    f"returned {found}: the query reached SQLite as a LIKE "
                    "pattern with its wildcards live"))

    def test_an_underscore_in_a_search_finds_only_itself(self) -> None:
        mm = _fresh_store()
        mm.store("project_notes", "in obsidian")
        mm.store("projectXnotes", "a different thing entirely")

        found = sorted(f.key for f in mm.search("project_notes"))

        self.assertEqual(found, ["project_notes"], (
            "a search for 'project_notes' also returned the key that differs "
            "from it only where the query holds an underscore"))


# ── the controls on the fix: green before the correction and after it ───────

class ALiteralMetacharacterStillNamesItsOwnFactTests(unittest.TestCase):
    """Escaping makes a metacharacter mean itself; it must not make it
    unmatchable. These are guards, green at both ends."""

    def test_a_subject_holding_a_percent_sign_forgets_the_fact_that_holds_it(self) -> None:
        mm = _fresh_store()
        mm.store("discount code", "SAVE10%")
        mm.store("editor", "vim")

        removed = mm.delete_by_key("SAVE10%")

        self.assertEqual(removed, 1, f"rows: {_rows(mm)}")
        self.assertEqual(_live_keys(mm), ["editor"])

    def test_a_subject_holding_an_underscore_forgets_the_fact_that_holds_it(self) -> None:
        mm = _fresh_store()
        mm.store("project_notes", "in obsidian")
        mm.store("editor", "vim")

        removed = mm.delete_by_key("project_notes")

        self.assertEqual(removed, 1, f"rows: {_rows(mm)}")
        self.assertEqual(_live_keys(mm), ["editor"])

    def test_a_subject_holding_the_escape_character_is_text_too(self) -> None:
        mm = _fresh_store()
        mm.store("windows share", "C:\\Users\\me")
        mm.store("editor", "vim")

        self.assertEqual(
            [f.key for f in mm.search("C:\\Users")], ["windows share"],
            "a backslash in the query must find a backslash in the fact")
        removed = mm.delete_by_key("C:\\Users")

        self.assertEqual(removed, 1, (
            "a backslash in the subject must match a backslash in the fact; "
            f"rows: {_rows(mm)}"))
        self.assertEqual(_live_keys(mm), ["editor"])


# ── the turn the user actually takes ────────────────────────────────────────

class TheRoutersMemoryTurnTakesOnlyWhatItNamesTests(unittest.TestCase):
    """The router's own memory turn, read back off the database file."""

    def test_forget_underscore_through_the_memory_turn_takes_nothing(self) -> None:
        mm = _two_unrelated_facts()

        result = _router(mm)._try_memory("forget _")

        self.assertTrue(result.handled, "control: the memory turn did not take "
                        "the sentence, so this case measured nothing")
        self.assertEqual(_live_keys(mm), ["backup drive", "editor"], (
            "the memory turn for 'forget _' removed facts the user never named; "
            f"it replied {result.text!r}"))
        self.assertIn("don't have any memories", result.text, (
            "the subject names nothing that was stored, so the true reply is "
            f"that there is no such memory; the turn said {result.text!r}"))

    def test_a_named_forget_through_the_memory_turn_still_forgets(self) -> None:
        """Control: green at both ends."""
        mm = _two_unrelated_facts()

        result = _router(mm)._try_memory("forget my backup drive")

        self.assertEqual(_live_keys(mm), ["editor"], (
            f"the named forget stopped working; the turn said {result.text!r}"))


# ── the shape of the defect, read off the syntax tree ───────────────────────

_SQL_LIKE = re.compile(r"\bLIKE\s+\?")
_DECLARED_ESCAPE = re.compile(r"\bESCAPE\s+'\\'")


class EveryLikeGivenTheUsersWordsDeclaresItsEscapeTests(unittest.TestCase):
    """Every SQL string in the memory module that applies LIKE to a bound
    parameter also declares the escape, so no later query in this module can
    take the user's words as a pattern again without this case saying so."""

    def test_every_like_on_a_parameter_declares_its_escape(self) -> None:
        with open(memory_module.__file__, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        likes = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Constant) and isinstance(node.value, str)
                 and _SQL_LIKE.search(node.value)]
        self.assertTrue(likes, (
            "control: no LIKE on a parameter was found in the memory module, so "
            "this case would pass by reading nothing"))
        undeclared = [(node.lineno, node.value) for node in likes
                      if not _DECLARED_ESCAPE.search(node.value)]
        self.assertEqual(undeclared, [], (
            "a LIKE in the memory module is given a parameter with no ESCAPE "
            f"clause, so a % or _ in the user's words is a wildcard there: "
            f"{undeclared}"))


if __name__ == "__main__":
    unittest.main()
