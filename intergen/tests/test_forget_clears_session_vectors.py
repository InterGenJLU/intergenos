# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A forget must reach the copy of the fact the running daemon holds in memory.

THE DEFECT, measured on the shipped tree in a throwaway HOME. The relevance
index keeps a cache of fact embeddings keyed by the VERBATIM "key: value" text
the user stored, so a fact is embedded once and reused. Nothing empties that
cache when the user asks InterGen to forget the fact. Measured: after "forget
about my backup drive" removed both stored rows and InterGen replied that it
had forgotten them, the drive path was still sitting in the cache of every
conversation's index — as the dictionary KEY, in the user's own words.

Three things make it last:
  * a conversation reset does not clear it, deliberately, because facts are
    cross-session and re-embedding them every reset would be waste;
  * every conversation has its OWN index, so a forget asked in one browser tab
    left the copies held by every other tab and by the desktop bus untouched;
  * an index is never collected, because the background worker it starts holds
    it. Dropping every reference to a discarded conversation leaves its cache
    alive for the life of the daemon.

WHAT THIS FILE DOES NOT ASSERT. This is a copy in process memory only. Nothing
here changes what is written to disk or when: whether a forgotten fact's BYTES
must leave the database file is the separate storage-contract question deferred
past this release, and the on-disk posture is exactly what it was.

The recall half is a CONTROL, not a defect: the candidate list handed to the
ranker is rebuilt from the store on every turn, so a row the store no longer
returns was already unrankable. Those cases PASS on the unfixed tree and are
here to keep passing.
"""
from __future__ import annotations

import gc
import os
import tempfile
import unittest
import weakref

from intergen.conversation_state import new_conversation_state
from intergen.memory import MemoryManager, SessionTurnIndex

_DRIVE = "/dev/disk/by-id/wwn-0x5000c500a1b2c3d4"
_FORGET = "forget about my backup drive"


class _Embedder:
    """A deterministic stand-in for the :8081 client. Each distinct text gets
    its own unit vector, so a query equal to a fact scores exactly 1.0 and any
    other pair scores 0.0 — what ranks is identity, never chance."""

    def __init__(self) -> None:
        self._axis: dict[str, int] = {}

    def __call__(self, texts):
        out = []
        for text in texts:
            axis = self._axis.setdefault(text, len(self._axis))
            vector = [0.0] * 64
            vector[axis % 64] = 1.0
            out.append(vector)
        return out


def _fresh_store() -> MemoryManager:
    """A MemoryManager on its own throwaway store, holding nothing else."""
    tmp = tempfile.mkdtemp(prefix="forget-vectors-")
    os.environ["XDG_DATA_HOME"] = os.path.join(tmp, "data")
    os.environ["XDG_STATE_HOME"] = os.path.join(tmp, "state")
    os.makedirs(os.environ["XDG_DATA_HOME"], exist_ok=True)
    os.makedirs(os.environ["XDG_STATE_HOME"], exist_ok=True)
    return MemoryManager()


def _facts_of(mm: MemoryManager) -> list[tuple[str, str]]:
    """The candidate list the router builds for the ranker, in its shape."""
    return [(f.fact_id, f"{f.key}: {f.value}") for f in mm.list_all()]


def _cached_texts(index: SessionTurnIndex) -> list[str]:
    """Every fact text this index is holding a vector for."""
    return sorted(index._fact_vecs)


class _IndexCase(unittest.TestCase):
    """Shared setup: a store holding two facts, and conversations whose caches
    have been warmed by asking each of them to rank those facts."""

    def setUp(self) -> None:
        self.mm = _fresh_store()
        self.mm.extract_and_store(f"remember that my backup drive is {_DRIVE}")
        self.mm.extract_and_store("remember that my editor is vim")
        self.assertGreater(self.mm.count, 0,
                           "control: nothing was stored, so this case would "
                           "measure a forget with nothing to forget")
        self.embedder = _Embedder()

    def _warm_conversation(self) -> SessionTurnIndex:
        """A new conversation whose index has ranked — and therefore cached —
        every fact currently in the store."""
        state = new_conversation_state(self.embedder)
        index = state.turn_index
        self.assertIsNotNone(index, "no index was built, so nothing is cached")
        self.addCleanup(index.stop)
        facts = _facts_of(self.mm)
        query = index.embed_query(facts[0][1])
        index.retrieve_facts(query, facts)
        self.assertTrue(any(_DRIVE in text for text in _cached_texts(index)),
                        "control: the cache was not warmed, so this case "
                        "would measure an empty dictionary")
        return index


class TheForgottenFactLeavesTheLiveIndex(_IndexCase):
    """The conversation the user typed into."""

    def test_no_cache_entry_holds_the_forgotten_text(self) -> None:
        index = self._warm_conversation()
        self.mm.format_forget_response(MemoryManager.is_forget_request(_FORGET))
        held = [text for text in _cached_texts(index) if _DRIVE in text]
        self.assertEqual(held, [], (
            "InterGen told the user it had forgotten the drive and is still "
            "holding the words it was told, in memory, as a cache key"))

    def test_the_fact_the_user_kept_keeps_its_vector(self) -> None:
        """The control on the fix: a forget takes what was named and no more.
        Clearing more than was forgotten would cost a needless re-embed on the
        next turn and would make the cache useless after any forget."""
        index = self._warm_conversation()
        self.mm.format_forget_response(MemoryManager.is_forget_request(_FORGET))
        kept = [text for text in _cached_texts(index) if "vim" in text]
        self.assertNotEqual(kept, [], (
            "the editor fact was not forgotten, and its cached vector was "
            "dropped anyway"))


class EveryLiveConversationDropsIt(_IndexCase):
    """One person, several surfaces. A forget asked in one browser tab must not
    leave the fact in the copy another tab and the desktop bus are holding."""

    def test_a_second_conversations_cache_is_cleared_too(self) -> None:
        asked_in = self._warm_conversation()
        other = self._warm_conversation()
        self.assertIsNot(asked_in, other,
                         "control: both names refer to one index, so this "
                         "case would not measure a second conversation")
        self.mm.format_forget_response(MemoryManager.is_forget_request(_FORGET))
        self.assertEqual(
            [text for text in _cached_texts(other) if _DRIVE in text], [],
            "the conversation that did not ask is still holding the fact")

    def test_a_discarded_conversations_cache_is_cleared_too(self) -> None:
        """An index outlives its conversation — the background worker it starts
        holds it, so nothing collects it. A cache the user can no longer reach
        still has to be emptied."""
        index = self._warm_conversation()
        alive = weakref.ref(index)
        del index
        gc.collect()
        survivor = alive()
        self.assertIsNotNone(survivor, (
            "control: the index was collected, so this case would not measure "
            "a discarded conversation's cache"))
        self.mm.format_forget_response(MemoryManager.is_forget_request(_FORGET))
        self.assertEqual(
            [text for text in _cached_texts(survivor) if _DRIVE in text], [],
            "a conversation the user ended is still holding the fact")


class ClearingEverythingClearsEveryCache(_IndexCase):
    """The "clear all my memories" path, which removes every stored fact."""

    def test_no_fact_vector_survives_a_full_clear(self) -> None:
        index = self._warm_conversation()
        self.mm.format_forget_response(
            MemoryManager.is_forget_request("clear all my memories"))
        self.assertEqual(_cached_texts(index), [], (
            "the user cleared every memory and the daemon is still holding "
            "their text"))


class AConversationResetStillKeepsWhatWasNotForgotten(_IndexCase):
    """A control on the boundary. Facts are cross-session: ending a
    conversation must NOT throw away the vectors of facts the user still has,
    or every reset would pay to embed them again."""

    def test_reset_keeps_the_cache_for_facts_that_were_not_forgotten(self) -> None:
        index = self._warm_conversation()
        before = _cached_texts(index)
        index.clear()
        self.assertEqual(_cached_texts(index), before, (
            "a conversation reset dropped fact vectors; facts outlive a "
            "conversation and re-embedding them every reset is waste"))


class TheRecallHalfWasAlreadyClosed(_IndexCase):
    """A CONTROL, passing before this lane and required to keep passing.

    The candidate list is rebuilt from the store on every turn, so a forgotten
    row is not a ranking candidate whatever the cache holds. These cases pin
    that guarantee; they are not evidence of a defect."""

    def test_the_forgotten_fact_cannot_be_ranked_afterwards(self) -> None:
        index = self._warm_conversation()
        self.mm.format_forget_response(MemoryManager.is_forget_request(_FORGET))
        remaining = _facts_of(self.mm)
        query = index.embed_query(f"my backup drive is {_DRIVE}")
        self.assertEqual(
            [text for text in index.retrieve_facts(query, remaining)
             if _DRIVE in text], [],
            "a forgotten fact was ranked into a later turn's prompt")

    def test_the_kept_fact_is_still_recalled(self) -> None:
        index = self._warm_conversation()
        self.mm.format_forget_response(MemoryManager.is_forget_request(_FORGET))
        remaining = _facts_of(self.mm)
        query = index.embed_query(remaining[0][1])
        self.assertNotEqual(index.retrieve_facts(query, remaining), [], (
            "a forget stopped an unrelated fact being recalled"))


class TheRecordSaysWhatWasCleared(_IndexCase):
    """A removal of the user's own data that the record does not mention is a
    hole in a writer whose whole mandate is that there are none. The forget row
    already names the stored rows it removed; it must also name the in-memory
    copies, which are a removal the user cannot otherwise see happening."""

    def test_the_forget_row_counts_the_cleared_vectors(self) -> None:
        import json
        from pathlib import Path

        import intergen.glass as glass

        tmp = tempfile.mkdtemp(prefix="forget-vectors-glass-")
        os.environ["XDG_DATA_HOME"] = os.path.join(tmp, "data")
        os.environ["XDG_STATE_HOME"] = os.path.join(tmp, "state")
        os.makedirs(os.environ["XDG_DATA_HOME"], exist_ok=True)
        os.makedirs(os.environ["XDG_STATE_HOME"], exist_ok=True)
        glass._glass = None
        self.mm = MemoryManager()
        self.mm.extract_and_store(f"remember that my backup drive is {_DRIVE}")
        self._warm_conversation()
        with glass.turn(glass.new_turn_id(), "dbus"):
            self.mm.format_forget_response(
                MemoryManager.is_forget_request(_FORGET))
        record = Path(os.environ["XDG_STATE_HOME"]) / "intergen" / "glass.jsonl"
        rows = [json.loads(line) for line in record.read_text().splitlines()] \
            if record.exists() else []
        # Named by (phase, event), never by position: the writer emits rows of
        # its own around a turn.
        forgets = [r for r in rows
                   if r.get("phase") == "memory" and r.get("event") == "forget"]
        self.assertEqual(len(forgets), 1, (
            "no memory/forget row in the record. Rows present: "
            + (", ".join(f"{r.get('phase')}/{r.get('event')}" for r in rows)
               or "none")))
        detail = forgets[0]["detail"]
        self.assertIn("session_vectors_cleared", detail, (
            "the record does not say the in-memory copies were removed; "
            "detail holds " + ", ".join(sorted(detail))))
        self.assertGreater(detail["session_vectors_cleared"], 0)


if __name__ == "__main__":
    unittest.main()
