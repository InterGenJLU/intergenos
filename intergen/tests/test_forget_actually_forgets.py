# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A fact the user asks InterGen to forget must stop being remembered.

THE DEFECT, measured on the shipped tree in a throwaway HOME. A user says
"remember that my backup drive is /dev/sdb1" and later "forget about my backup
drive". The store keeps two rows for that one sentence — the extractor writes
one under the perspective-shifted key ("your backup drive") and one under the
bare noun ("backup drive"). The forget path takes the subject VERBATIM from the
user's sentence, "my backup drive", and asks the store for rows whose key or
value contains that string. Neither stored key does. So nothing is deleted, the
fact is still recalled afterwards, and the user is told:

    "I don't have any memories about 'my backup drive'."

Which is false twice over: the memory exists, and it was not forgotten. This is
the same behaviour a live 9B run recorded on 2026-07-17 — "forget that" followed
by a restart, the fact still recalled, deleted=0 in the store — and it is still
here.

Saying the exact stored words did not close it either: "forget about your
backup drive" deleted the row under that key and left the row under "backup
drive", so the same fact was still returned under its other name.

SINCE 2026-08-26 THE STORE SIDE NO LONGER WRITES THE TWIN. One stated fact
makes one row, keyed "backup drive" (test_one_fact_one_key.py). The forget
defect this file is about is unchanged by that and these cases still measure it:
the subject still arrives in the user's words ("my backup drive"), and it still
has to reach a row keyed something else.

WHAT THIS FILE DOES NOT ASSERT. Whether a forgotten fact's BYTES must leave the
database file is a separate question, and it is settled for this release
elsewhere: the storage contract is scheduled after R001.2. These cases are about
the fact still being REMEMBERED — an answer InterGen will still give — not about
what remains on disk after it stops giving it.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

from intergen.memory import MemoryManager

_DRIVE = "/dev/disk/by-id/wwn-0x5000c500a1b2c3d4"


def _fresh_store() -> MemoryManager:
    """A MemoryManager on its own throwaway store, with nothing else in it."""
    tmp = tempfile.mkdtemp(prefix="forget-")
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


class TheUsersOwnPhrasingForgets(unittest.TestCase):
    """The sentence pair a person actually types, both halves of it."""

    def setUp(self) -> None:
        self.mm = _fresh_store()
        self.mm.extract_and_store(f"remember that my backup drive is {_DRIVE}")
        self.assertGreater(self.mm.count, 0,
                           "control: nothing was stored, so this case would "
                           "measure a forget with nothing to forget")

    def test_the_fact_is_no_longer_recalled(self) -> None:
        self.mm.format_forget_response(
            MemoryManager.is_forget_request("forget about my backup drive"))
        still = [(k, v) for k, v, deleted in _rows(self.mm)
                 if not deleted and _DRIVE in v]
        self.assertEqual(still, [], (
            "after the user asked InterGen to forget it, the drive is still an "
            "active fact and will still be recalled"))

    def test_no_active_row_of_that_sentence_survives(self) -> None:
        """Both rows, not just the one whose key happens to match."""
        self.mm.format_forget_response(
            MemoryManager.is_forget_request("forget about my backup drive"))
        self.assertEqual(self.mm.count, 0, _rows(self.mm))

    def test_the_reply_does_not_claim_there_was_nothing(self) -> None:
        reply = self.mm.format_forget_response(
            MemoryManager.is_forget_request("forget about my backup drive"))
        self.assertNotIn("don't have any memories", reply, (
            "InterGen told the user it had no such memory while holding it. A "
            "false statement about the user's own data is worse than the "
            "failure it is reporting on"))
        self.assertIn("forgotten", reply)


class SayingTheStoredWordsForgetsAllOfIt(unittest.TestCase):
    """The other half: an exactly-matching subject must not leave a twin.

    The twin rows this case was written against are gone at the store side —
    one stated fact now makes one row (test_one_fact_one_key.py). The case is
    kept because the forget path must still reach a fact by a name that is not
    the stored key: a person who says "forget about your backup drive" is using
    InterGen's own words for it, and the row is keyed "backup drive".
    """

    def test_the_bare_noun_row_goes_too(self) -> None:
        mm = _fresh_store()
        mm.extract_and_store(f"remember that my backup drive is {_DRIVE}")
        mm.format_forget_response(
            MemoryManager.is_forget_request("forget about your backup drive"))
        self.assertIsNone(mm.get("backup drive"), (
            "a forget phrased in InterGen's own words for the fact left "
            "InterGen able to answer with it"))


class ForgettingIsNotTooGreedy(unittest.TestCase):
    """The control on the fix. A forget must take what was named and no more."""

    def test_an_unrelated_fact_is_left_alone(self) -> None:
        mm = _fresh_store()
        mm.extract_and_store(f"remember that my backup drive is {_DRIVE}")
        mm.extract_and_store("remember that my editor is vim")
        mm.format_forget_response(
            MemoryManager.is_forget_request("forget about my backup drive"))
        self.assertEqual(mm.get("editor"), "vim",
                         "a forget took a fact the user did not name")

    def test_a_subject_that_matches_nothing_says_so(self) -> None:
        mm = _fresh_store()
        mm.extract_and_store(f"remember that my backup drive is {_DRIVE}")
        reply = mm.format_forget_response(
            MemoryManager.is_forget_request("forget about my motorcycle"))
        self.assertIn("don't have any memories", reply)
        self.assertGreater(mm.count, 0,
                           "a forget for something not stored removed "
                           "something that was")


class ClearingEverythingClearsEverything(unittest.TestCase):
    """The __ALL__ path, which does work today — a control, not a defect."""

    def test_clear_all_leaves_no_active_fact(self) -> None:
        mm = _fresh_store()
        mm.extract_and_store(f"remember that my backup drive is {_DRIVE}")
        mm.extract_and_store("remember that my editor is vim")
        reply = mm.format_forget_response(
            MemoryManager.is_forget_request("clear all my memories"))
        self.assertIn("cleared", reply)
        self.assertEqual(mm.count, 0)


class AForgetIsVisibleInTheRecord(unittest.TestCase):
    """A deletion of the user's own data that the record does not mention is a
    hole in a writer whose whole mandate is that there are none."""

    def test_a_forget_emits_a_row_naming_what_it_removed(self) -> None:
        import json
        from pathlib import Path

        import intergen.glass as glass

        tmp = tempfile.mkdtemp(prefix="forget-glass-")
        os.environ["XDG_DATA_HOME"] = os.path.join(tmp, "data")
        os.environ["XDG_STATE_HOME"] = os.path.join(tmp, "state")
        os.makedirs(os.environ["XDG_DATA_HOME"], exist_ok=True)
        os.makedirs(os.environ["XDG_STATE_HOME"], exist_ok=True)
        glass._glass = None
        mm = MemoryManager()
        mm.extract_and_store(f"remember that my backup drive is {_DRIVE}")
        with glass.turn(glass.new_turn_id(), "dbus"):
            mm.format_forget_response(
                MemoryManager.is_forget_request("forget about my backup drive"))
        record = (Path(os.environ["XDG_STATE_HOME"]) / "intergen" / "glass.jsonl")
        rows = [json.loads(x) for x in record.read_text().splitlines()] \
            if record.exists() else []
        # Named by (phase, event), never by position: the writer emits rows of
        # its own around a turn, so the row this case means is not at any fixed
        # index.
        forgets = [r for r in rows
                   if r.get("phase") == "memory" and r.get("event") == "forget"]
        self.assertEqual(len(forgets), 1, (
            "no memory/forget row in the record. Rows present: "
            + (", ".join(f"{r.get('phase')}/{r.get('event')}" for r in rows)
               or "none")))
        self.assertIn("removed", forgets[0]["detail"])
        self.assertGreater(forgets[0]["detail"]["removed"], 0)


if __name__ == "__main__":
    unittest.main()
