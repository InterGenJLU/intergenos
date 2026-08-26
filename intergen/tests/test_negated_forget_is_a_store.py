# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A sentence that says NOT to forget something must never delete it.

THE DEFECT, read in the tree and reproduced here. The forget classifier's first
pattern is ``r"forget (?:about |that )?(.+)"`` (``intergen/memory.py``, the
``_FORGET_PATTERNS`` list) and it is applied with ``re.search`` in
``MemoryManager.is_forget_request``. ``search`` matches anywhere in the
sentence, so the four words "forget that my keyboard layout is Colemak" inside

    don't forget that my keyboard layout is Colemak

match, and the text after the verb is returned as the subject to DELETE. The
router asks the forget classifier BEFORE the remember classifier
(``intergen/router.py``, ``_try_memory``), so the delete answer wins even though
``MemoryManager.is_remember_request`` lists "don't forget" as a STORE trigger:
two classifiers claim the same sentence and the destructive one is consulted
first.

WHAT THE USER SEES, measured on the tree at this branch's base. Two shapes, and
the second one loses data:

  "don't forget that my keyboard layout is Colemak" — the captured subject is
  the whole tail, "my keyboard layout is Colemak", which matches no stored key,
  so the reply is "I don't have any memories about 'my keyboard layout is
  Colemak'." Wrong answer, nothing deleted.

  "don't forget my keyboard layout" — the captured subject is "my keyboard
  layout", which DOES match the stored rows. Both rows are marked deleted and
  the reply is "Done. I've forgotten 2 things about 'my keyboard layout'." The
  user asked InterGen to keep the fact and it was destroyed instead.

SCOPE. "don't forget", "do not forget", "dont forget" and "never forget" all
reach the delete path today, at any position in the sentence, and the plain
"forget about X" request must keep working unchanged.

TIERS. The classifier and its pattern list are module-level and static, and
``_try_memory`` reads neither the hardware tier nor the dispatch lock, so the
behaviour cannot differ between the 2B, 9B and 35B configurations. That is a
reading, so every case below is also RUN under all three tier configurations,
each derived from the same resolver the daemon uses
(``intergen.dispatch_policy.resolve_dispatch_for_model``).
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

from intergen.dispatch_policy import resolve_dispatch_for_model
from intergen.interfaces.types import HardwareTierLevel
from intergen.memory import MemoryManager
from intergen.router import ConversationRouter

_LAYOUT = "Colemak"

# "Keep this" sentences that also STATE the fact. These are stores.
_KEEP_IT_WITH_FACT = [
    "don't forget that my keyboard layout is Colemak",
    "do not forget that my keyboard layout is Colemak",
    "dont forget that my keyboard layout is Colemak",
    "never forget that my keyboard layout is Colemak",
    "please don't forget that my keyboard layout is Colemak",
    "don't ever forget that my keyboard layout is Colemak",
]

# "Keep this" sentences that NAME an already-stored fact instead of restating
# it. These are the data-loss case: measured on the tree at this branch's base,
# "don't forget my keyboard layout" captures "my keyboard layout" as the subject
# to delete, marks both stored rows deleted and answers "Done. I've forgotten 2
# things about 'my keyboard layout'." The user asked for the opposite.
_KEEP_IT_BARE = [
    "don't forget my keyboard layout",
    "do not forget my keyboard layout",
    "dont forget my keyboard layout",
    "never forget my keyboard layout",
    "please don't forget my keyboard layout",
]

_KEEP_IT = _KEEP_IT_WITH_FACT + _KEEP_IT_BARE

# The plain delete request, which must keep deleting.
_DELETE_IT = "forget about my keyboard layout"


def _tier_configs() -> list[tuple[str, HardwareTierLevel, bool]]:
    """(label, hardware_tier, lock_dispatch) for the 2B, 9B and 35B boxes.

    Derived from the resolver the daemon calls, not hand-written, so a change to
    the dispatch policy is reflected here instead of drifting away from it.
    """
    out = []
    for label, level in (("2B", HardwareTierLevel.TIER_1),
                         ("9B", HardwareTierLevel.TIER_2),
                         ("35B", HardwareTierLevel.TIER_3)):
        res = resolve_dispatch_for_model(level, detected_tier=level)
        out.append((label, res.tier, res.lock_dispatch))
    return out


def _fresh_store() -> MemoryManager:
    """A MemoryManager on its own throwaway store, with nothing else in it."""
    tmp = tempfile.mkdtemp(prefix="negated-forget-")
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


def _router(mm: MemoryManager, hardware_tier: HardwareTierLevel,
            lock_dispatch: bool) -> ConversationRouter:
    """The real router's memory path, with only what that path reads set.

    ``_try_memory`` is reached with no model, no tools and no matcher, so the
    turn is decided by the same code the daemon runs without loading any of the
    machinery a full construction would. The conversation is left unset on
    purpose: a router built this way gives itself a fresh one on first read, so
    each case starts with no pending offer from a previous case.
    """
    r = ConversationRouter.__new__(ConversationRouter)
    r._memory = mm
    r._hardware_tier = hardware_tier
    r._lock_dispatch = lock_dispatch
    assert r._conv.pending_memory_offer is None
    return r


class NegatedForgetIsNotADeleteRequest(unittest.TestCase):
    """The classifier itself: "don't forget X" is not a forget request."""

    def test_the_classifier_declines_every_negated_sentence(self) -> None:
        for sentence in _KEEP_IT:
            with self.subTest(sentence=sentence):
                self.assertIsNone(
                    MemoryManager.is_forget_request(sentence),
                    f"{sentence!r} was classified as a request to DELETE "
                    f"{MemoryManager.is_forget_request(sentence)!r}")

    def test_the_plain_delete_request_still_classifies(self) -> None:
        # Control: the guard must not switch the real delete path off.
        self.assertEqual(
            MemoryManager.is_forget_request(_DELETE_IT), "my keyboard layout")


class NegatedForgetDoesNotDeleteTheFact(unittest.TestCase):
    """The data-loss case: the fact exists, and the sentence must not remove it."""

    def test_a_stored_fact_survives_every_negated_sentence(self) -> None:
        for label, tier, lock in _tier_configs():
            for sentence in _KEEP_IT:
                with self.subTest(tier=label, sentence=sentence):
                    mm = _fresh_store()
                    mm.extract_and_store(
                        f"remember that my keyboard layout is {_LAYOUT}")
                    before = [(k, v) for k, v, deleted in _rows(mm)
                              if not deleted and _LAYOUT in v]
                    self.assertNotEqual(before, [], "control: nothing stored")
                    self.assertGreater(
                        mm.count, 0,
                        "control: nothing was stored, so this case would "
                        "measure a delete with nothing to delete")
                    router = _router(mm, tier, lock)
                    router._try_memory(sentence)
                    live = [(k, v) for k, v, deleted in _rows(mm)
                            if not deleted and _LAYOUT in v]
                    self.assertNotEqual(
                        live, [],
                        f"[{label}] {sentence!r} deleted the fact the user "
                        f"asked to keep")

    def test_the_reply_is_not_the_delete_reply(self) -> None:
        for label, tier, lock in _tier_configs():
            for sentence in _KEEP_IT:
                with self.subTest(tier=label, sentence=sentence):
                    mm = _fresh_store()
                    router = _router(mm, tier, lock)
                    result = router._try_memory(sentence)
                    self.assertNotIn(
                        "I don't have any memories about", result.text or "",
                        f"[{label}] {sentence!r} was answered with the "
                        f"failed-delete reply")
                    self.assertNotIn(
                        "I've forgotten", result.text or "",
                        f"[{label}] {sentence!r} was answered as a delete")


class NegatedForgetStoresTheFact(unittest.TestCase):
    """The sentence means "keep this", so the fact must end up in the store."""

    def test_the_fact_is_stored_on_every_tier(self) -> None:
        for label, tier, lock in _tier_configs():
            for sentence in _KEEP_IT_WITH_FACT:
                with self.subTest(tier=label, sentence=sentence):
                    mm = _fresh_store()
                    router = _router(mm, tier, lock)
                    result = router._try_memory(sentence)
                    self.assertTrue(
                        result.handled,
                        f"[{label}] {sentence!r} was not handled by the "
                        f"memory route at all")
                    live = [(k, v) for k, v, deleted in _rows(mm)
                            if not deleted and _LAYOUT in v]
                    self.assertNotEqual(
                        live, [],
                        f"[{label}] {sentence!r} stored nothing, so the fact "
                        f"the user asked to keep is not kept")


class PlainForgetStillDeletes(unittest.TestCase):
    """Control on every tier: the real delete request is unchanged."""

    def test_the_fact_is_deleted(self) -> None:
        for label, tier, lock in _tier_configs():
            with self.subTest(tier=label):
                mm = _fresh_store()
                mm.extract_and_store(
                    f"remember that my keyboard layout is {_LAYOUT}")
                router = _router(mm, tier, lock)
                result = router._try_memory(_DELETE_IT)
                self.assertTrue(result.handled,
                                f"[{label}] the delete request was not handled")
                live = [(k, v) for k, v, deleted in _rows(mm)
                        if not deleted and _LAYOUT in v]
                self.assertEqual(
                    live, [],
                    f"[{label}] the fact is still active after the user asked "
                    f"for it to be forgotten")


if __name__ == "__main__":
    unittest.main()
