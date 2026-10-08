# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A forget subject is a subject, not a pattern: LIKE metacharacters in it must
not widen what is deleted.

THE DEFECT, read on the tree at intergen/memory.py:848. ``delete_by_key``
builds its match clause as::

    clause = " OR ".join(["key LIKE ? OR value LIKE ?"] * len(forms))

and fills it with ``f"%{form}%"`` for each form :meth:`forget_forms` returned.
The subject arrives in the user's own words and goes into a LIKE pattern with
no escaping of the two LIKE metacharacters ``%`` and ``_``, and no ESCAPE
clause on the operator. In SQLite ``_`` matches any single character, so a
subject of ``_`` becomes the pattern ``%_%``, which matches every key of at
least one character — that is, every stored fact. A subject of ``%`` becomes
``%%%`` and does the same.

WHAT THAT COSTS. "forget _" is routed by :meth:`is_forget_request` to the
subject ``'_'`` (the first forget pattern's group is ``(.+)``), and is NOT the
``__ALL__`` subject, so it does not take the clear-everything path the user can
see. It takes the narrow path and silently empties the store: every active row
soft-deleted, every named fact vector dropped, and the reply "Done. I've
forgotten N things about '_'." The record's memory/forget row carries
``subject: "_"`` with ``removed: N``, so a full wipe is written down as an
ordinary narrow forget.

WHAT THE CODE ITSELF PROMISES. :meth:`forget_forms` states the contract these
cases hold it to (intergen/memory.py:822-823): the forms "WIDEN what a forget
matches, and that is the point — but only as far as the store side widened what
it wrote. A subject naming nothing the user stored still matches nothing." A
subject of ``_`` names nothing that was stored. It must match nothing.

THE FIX these cases are written against is to escape ``\\``, ``%`` and ``_`` in
each form and pass ``ESCAPE`` on every LIKE in the clause. The last case is the
control on that fix: the ordinary forget the file
test_forget_actually_forgets.py measures must keep working, so escaping must not
be done by rejecting or truncating the subject.

WHAT THIS FILE DOES NOT ASSERT. Nothing about the bytes on disk — the deletion
is soft by design and the storage contract is settled elsewhere. These cases are
about WHICH rows a forget takes.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

from intergen.memory import MemoryManager


def _fresh_store() -> MemoryManager:
    """A MemoryManager on its own throwaway store, with nothing else in it."""
    tmp = tempfile.mkdtemp(prefix="forget-wildcard-")
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


def _two_unrelated_facts() -> MemoryManager:
    """Two facts sharing no substring, neither of them named by any wildcard."""
    mm = _fresh_store()
    mm.store("editor", "vim")
    mm.store("backup drive", "/dev/sdb1")
    return mm


class AWildcardSubjectNamesNothing(unittest.TestCase):
    """The subject is matched as text, so a metacharacter in it is text."""

    def test_a_forget_subject_of_wildcards_deletes_nothing_it_did_not_name(self) -> None:
        mm = _two_unrelated_facts()
        self.assertEqual(mm.count, 2, "control: the two facts were not stored")

        removed = mm.delete_by_key("_")

        self.assertEqual(removed, 0, (
            "the subject '_' names no stored fact, yet delete_by_key reported "
            f"removing {removed}: the underscore reached SQLite as the "
            "single-character LIKE wildcard in the pattern '%_%', which matches "
            f"every key of at least one character. Rows: {_rows(mm)}"))
        self.assertEqual(mm.count, 2, (
            "a forget for '_' emptied the store of facts the user never named. "
            f"Rows: {_rows(mm)}"))

    def test_a_forget_subject_of_percent_deletes_nothing_it_did_not_name(self) -> None:
        mm = _two_unrelated_facts()
        self.assertEqual(mm.count, 2, "control: the two facts were not stored")

        removed = mm.delete_by_key("%")

        self.assertEqual(removed, 0, (
            "the subject '%' names no stored fact, yet delete_by_key reported "
            f"removing {removed}: it reached SQLite as the any-run LIKE "
            f"wildcard in the pattern '%%%'. Rows: {_rows(mm)}"))
        self.assertEqual(mm.count, 2, (
            "a forget for '%' emptied the store of facts the user never named. "
            f"Rows: {_rows(mm)}"))

    def test_an_underscore_inside_a_subject_stays_a_literal(self) -> None:
        """The same leak on a subject a person would plausibly type.

        Two keys that differ only where the subject has its underscore. The
        subject names one of them; the wildcard reading names both.
        """
        mm = _fresh_store()
        mm.store("project_notes", "in obsidian")
        mm.store("projectXnotes", "a different thing entirely")

        removed = mm.delete_by_key("project_notes")

        self.assertEqual(removed, 1, (
            "the subject 'project_notes' names exactly one stored key, yet "
            f"delete_by_key reported removing {removed}: the underscore matched "
            f"the 'X' in the other key as well. Rows: {_rows(mm)}"))
        self.assertEqual(mm.get("projectXnotes"), "a different thing entirely", (
            "a forget took a fact whose key the subject did not name — it "
            "differs from the subject exactly where the subject holds a LIKE "
            "metacharacter"))


class TheUserFacingPathSaysWhatItDid(unittest.TestCase):
    """The same defect reached the way a person reaches it, and the reply."""

    def test_forget_underscore_is_not_a_clear_all_in_disguise(self) -> None:
        mm = _two_unrelated_facts()

        subject = MemoryManager.is_forget_request("forget _")
        self.assertEqual(subject, "_", (
            "reachability control: the forget route did not hand the bare "
            f"subject to the store, it handed {subject!r}"))
        self.assertNotEqual(subject, "__ALL__", (
            "reachability control: this sentence must NOT be the visible "
            "clear-everything path, or the case is measuring clear_all"))

        reply = mm.format_forget_response(subject)

        self.assertEqual(mm.count, 2, (
            "'forget _' wiped the whole store down the narrow forget path, "
            f"which the user never asked for. Reply was {reply!r}; "
            f"rows: {_rows(mm)}"))
        self.assertIn("don't have any memories", reply, (
            "the subject '_' names nothing that was stored, so the truthful "
            f"reply is that there is no such memory; InterGen said {reply!r}"))


class TheOrdinaryForgetStillForgets(unittest.TestCase):
    """The control on the fix: escaping the subject must not narrow a real
    forget. This case passes before the fix and must keep passing after it."""

    def test_the_users_own_phrasing_still_removes_its_one_row(self) -> None:
        mm = _fresh_store()
        mm.extract_and_store("remember that my backup drive is /dev/sdb1")
        self.assertGreater(mm.count, 0, "control: nothing was stored")

        removed = mm.delete_by_key("my backup drive")

        self.assertGreater(removed, 0, (
            "the forget the user actually types stopped working: "
            f"rows {_rows(mm)}"))
        self.assertIsNone(mm.get("backup drive"),
                          "the named fact is still active and will be recalled")


if __name__ == "__main__":
    unittest.main()
