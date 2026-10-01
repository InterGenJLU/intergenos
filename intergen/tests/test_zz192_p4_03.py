# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A forget that NAMES a subject must never be executed as a full wipe.

THE DEFECT, at intergen/memory.py:1113. Inside the forget-pattern loop, after a
pattern has already matched and captured a named subject, two bare SUBSTRING
tests override that subject with the full-reset sentinel:

    if "clear" in message.lower() and ("all" in message.lower()
                                         or "memories" in message.lower()):
        return "__ALL__"

Neither test asks for a word. "clear" is a substring of "unclear", "nuclear"
and "clearance"; "all" is a substring of "wallpaper", "install", "fallback",
"small" and "really". So a sentence that names ONE thing to forget and never
asks for a reset is served by the reset path:

    "forget my unclear wallpaper"      -> pattern 1 captures "my unclear
                                          wallpaper", then "clear" is found in
                                          "unclear" and "all" in "wallpaper"

``format_forget_response`` then calls ``clear_all()``, which marks EVERY row in
the fact table deleted and empties every live index's fact-vector cache, and
reports it back to the user as the thing they asked for:

    "Done. I've cleared all 2 memories."

That is the user's whole memory gone, on a sentence that asked for one fact to
go, described to them as their own request. A deletion of the user's own data
is not recoverable, which is why the two guards already standing in front of
these patterns exist (the store classifier, and the negated-verb check) — this
line sits BEHIND both of them and undoes the subject they let through.

WHAT THIS FILE ASSERTS. Only that a named subject stays the subject, and that a
fact the user did not name survives. The control class measures the other half:
a sentence that really does ask for a reset must still get one.

WHAT IT DOES NOT ASSERT. Whether a named forget then MATCHES any stored row is
a separate question with its own file (test_forget_actually_forgets.py). A
subject naming nothing stored is allowed to remove nothing; what is not allowed
is for it to remove everything.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

from intergen.memory import MemoryManager


def _fresh_store() -> MemoryManager:
    """A MemoryManager on its own throwaway store, with nothing else in it."""
    tmp = tempfile.mkdtemp(prefix="forget-substring-")
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


def _two_facts() -> MemoryManager:
    """A store holding two unrelated facts the user stated and kept."""
    mm = _fresh_store()
    mm.extract_and_store("remember that my editor is vim")
    mm.extract_and_store("remember that my wallpaper is dark")
    assert mm.count == 2, (
        "control: the two facts were not stored, so this case would measure a "
        f"forget with nothing to lose (rows: {_rows(mm)})")
    return mm


class ASubstringIsNotARequestToClearEverything(unittest.TestCase):
    """The sentence names one thing. The subject it named must be the subject."""

    def test_a_named_forget_is_not_turned_into_a_full_wipe_by_a_substring(self) -> None:
        self.assertEqual(
            MemoryManager.is_forget_request("forget my unclear wallpaper"),
            "my unclear wallpaper",
            "the sentence names one thing to forget and asks for no reset; the "
            "full-reset sentinel came back because 'clear' is inside 'unclear' "
            "and 'all' is inside 'wallpaper'")

    def test_the_delete_the_memory_about_form_is_not_a_wipe_either(self) -> None:
        self.assertEqual(
            MemoryManager.is_forget_request(
                "delete the memory about my unclear install path"),
            "my unclear install path",
            "'clear' inside 'unclear' and 'all' inside 'install' turned a "
            "single named deletion into a full reset")

    def test_a_third_substring_pair_does_not_reset_the_store_either(self) -> None:
        self.assertEqual(
            MemoryManager.is_forget_request("forget my nuclear fallback plan"),
            "my nuclear fallback plan",
            "'clear' inside 'nuclear' and 'all' inside 'fallback' turned a "
            "single named deletion into a full reset")

    def test_a_fact_the_user_did_not_name_survives(self) -> None:
        mm = _two_facts()
        mm.format_forget_response(
            MemoryManager.is_forget_request("forget my unclear wallpaper"))
        self.assertEqual(mm.get("editor"), "vim", (
            "the user asked for one thing to be forgotten and lost every "
            f"stored fact (rows: {_rows(mm)})"))

    def test_the_store_is_not_emptied_by_a_named_forget(self) -> None:
        mm = _two_facts()
        mm.format_forget_response(
            MemoryManager.is_forget_request("forget my unclear wallpaper"))
        self.assertGreater(mm.count, 0, (
            "a named forget emptied the whole fact table "
            f"(rows: {_rows(mm)})"))

    def test_the_reply_does_not_report_a_reset_the_user_never_asked_for(self) -> None:
        mm = _two_facts()
        reply = mm.format_forget_response(
            MemoryManager.is_forget_request("forget my unclear wallpaper"))
        self.assertNotIn("cleared all", reply, (
            "InterGen told the user it had cleared all their memories in "
            "answer to a sentence that named one fact. A false statement "
            "about the user's own data is worse than the loss it reports"))


class ControlARealResetStillResets(unittest.TestCase):
    """The instrument's own control: the sentence that DOES ask for a reset must
    still get one, or the case above would pass on a broken forget path."""

    def test_clear_all_my_memories_is_still_a_full_reset(self) -> None:
        self.assertEqual(
            MemoryManager.is_forget_request("clear all my memories"), "__ALL__")

    def test_clear_my_memories_is_still_a_full_reset(self) -> None:
        self.assertEqual(
            MemoryManager.is_forget_request("clear my memories"), "__ALL__")

    def test_a_real_reset_leaves_no_active_fact(self) -> None:
        mm = _two_facts()
        reply = mm.format_forget_response(
            MemoryManager.is_forget_request("clear all my memories"))
        self.assertIn("cleared", reply)
        self.assertEqual(mm.count, 0, _rows(mm))

    def test_an_ordinary_named_forget_still_names_its_subject(self) -> None:
        self.assertEqual(
            MemoryManager.is_forget_request("forget about my backup drive"),
            "my backup drive")


if __name__ == "__main__":
    unittest.main()
