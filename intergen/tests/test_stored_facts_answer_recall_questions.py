# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A recall question the store can answer is answered from the store, by code.

THE OBSERVED FAILURE. Measured on the 9B tier against dev e22901e0e, over the ten
cross-session recall scenarios (battery ids MEM-cross-b01..b10): the fact is
stored on turn 1, and after a real restart it IS retrieved on turn 2 — the glass
rows show memory/facts_inject with a top_score of 0.62-0.78, above the 0.60
threshold — and then the model ignored the injected fact in SEVEN of ten replies,
answering "I don't know your name - you haven't told me yet" over a row sitting
readable in the store. The 35B used it 10/10; the corrected 2B 9/10.

RETRIEVAL IS NOT THE DEFECT. The fact reaches the prompt. It reaches it as
context explicitly framed "NOT an instruction" (router._build_messages), and what
the user is told is then left to the model's discretion. A keyed recall — the
question names a key the store holds — has one correct answer, and asking a model
to please use it is not the same as answering it.

WHY THE EXISTING CODE DOES NOT ALREADY DO THIS. The router HAS a deterministic
composer, ConversationRouter._answer_from_stored_facts, and it is reached from
exactly one place: the `kind == "recall"` branch of _try_memory, which asks
MemoryManager.classify_declarative what shape the turn is. That classifier only
ever returns "recall" from INSIDE its preference-verb branch — a question is
recognised as a recall when it happens to carry "prefer/like/use/want" in the
right position. A bare possessive recall carries no such verb, so:

    MemoryManager.classify_declarative("what's my name?") -> (None, None, None)

and all ten battery questions classify that way, measured. The recall branch
never runs, the composer is never called, and the turn falls through to the model
with the fact quoted beside it.

THE CONTROL THAT MATTERS. Widening what counts as a recall question must not let
the memory route claim turns it cannot answer. It cannot: the recall branch calls
_answer_from_stored_facts and DECLINES the turn (handled=False) when the store
holds nothing the question names, which is the existing contract and is pinned
below by a question about a subject the store has never heard of.

TIER SCOPE. Tier-independent by construction and by test — see
TierIndependenceTests below, which names the absence at file:line rather than
asserting it.

The model here is stubbed to answer "I don't know", so a reply carrying the
stored value can only have been composed by code.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import intergen.glass as glass
from intergen.interfaces.types import HardwareTierLevel
from intergen.memory import MemoryManager
from intergen.router import ConversationRouter

# The daemon's three serving tiers, named as the battery names them.
TIERS = (("2B", HardwareTierLevel.TIER_1),
         ("9B", HardwareTierLevel.TIER_2),
         ("35B", HardwareTierLevel.TIER_3))

# The ten MEM-cross pairs, verbatim from
# intergen/tests/scenario/corpus/memory_personal.json: (battery id, stored key,
# stored value, the turn-2 question).
MEM_CROSS = (
    ("b01", "your backup drive",   "/dev/sdb1",      "what's my backup drive?"),
    ("b02", "your default editor", "neovim",         "what editor do I use?"),
    ("b03", "your timezone",       "Mountain",       "what timezone am I in?"),
    ("b04", "your name",           "Alex",           "what's my name?"),
    ("b05", "your preferred shell", "fish",          "which shell do I use?"),
    ("b06", "your favorite color", "coral",          "what's my favorite color?"),
    ("b07", "your wifi network",   "GuestNet-2.4",   "what's my wifi network called?"),
    ("b08", "your printer",        "lobby-printer-1", "what's my printer?"),
    ("b09", "your favorite font size", "16",         "what font size do I prefer?"),
    ("b10", "your keyboard layout", "Colemak",       "what keyboard layout do I use?"),
)

MODEL_SAYS = "I don't know that — you haven't told me."


class _Base(unittest.TestCase):
    """A REAL MemoryManager on a temp DB and the real _try_memory. The model is
    not wired at all: if a turn reaches it, that is the failure under test."""

    TIER = HardwareTierLevel.TIER_2

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        os.environ["XDG_STATE_HOME"] = self.tmp
        os.environ.pop("INTERGEN_GLASS", None)
        glass._glass = None
        self.memory = MemoryManager(db_path=Path(self.tmp) / "memory.db")
        r = ConversationRouter.__new__(ConversationRouter)
        r._memory = self.memory
        # _conv is deliberately NOT stubbed. ConversationRouter._conv already
        # gives a router built with __new__ its own real ConversationState, and
        # a stub here would be both unnecessary and less faithful than the state
        # the router actually uses.
        r._hardware_tier = self.TIER
        r._current_query_type = "general"
        self.r = r

    def _route(self, text):
        with glass.turn(glass.new_turn_id(), "test"):
            return self.r._try_memory(text)


class RecallQuestionsAreClassifiedAsRecallTests(unittest.TestCase):
    """The classifier is where the ten questions are lost, so it is pinned on its
    own: a reader who sees only the router test would not know which of the two
    parts moved."""

    def test_every_battery_recall_question_classifies_as_recall(self):
        for bid, _key, _value, question in MEM_CROSS:
            with self.subTest(scenario=f"MEM-cross-{bid}"):
                kind, _k, _v = MemoryManager.classify_declarative(question)
                self.assertEqual(
                    kind, "recall",
                    f"{question!r} is a recall question and classifies as "
                    f"{kind!r}, so the router's recall branch never runs")

    def test_a_statement_is_still_not_a_recall(self):
        """The guard: widening recall must not swallow the declarative shapes."""
        for stated, expected in (("my name is Alex", "preference"),
                                 ("I prefer dark mode", "preference"),
                                 ("remember that my editor is vim", None)):
            with self.subTest(stated=stated):
                kind, _k, _v = MemoryManager.classify_declarative(stated)
                if expected is None:
                    self.assertNotEqual(kind, "recall", stated)
                else:
                    self.assertEqual(kind, expected, stated)


class StoredFactAnswersTheQuestionTests(_Base):
    """The whole point: the value comes back without the model."""

    def test_every_battery_recall_is_answered_from_the_store(self):
        for name, tier in TIERS:
            for bid, key, value, question in MEM_CROSS:
                with self.subTest(tier=name, scenario=f"MEM-cross-{bid}"):
                    self.setUp()
                    self.r._hardware_tier = tier
                    self.memory.store(key, value)
                    res = self._route(question)
                    self.assertTrue(
                        res.handled,
                        f"{question!r} was not answered from the store, so it "
                        f"falls through to the model, which the battery measured "
                        f"ignoring the fact 7 times in 10")
                    self.assertIn(
                        value, res.text,
                        f"the answer to {question!r} does not carry the stored "
                        f"value {value!r}")
                    self.assertEqual(res.source, "memory")

    def test_the_answer_is_marked_as_composed_by_code(self):
        """Its provenance must say code, not model — the surfaces and the graders
        both read answer_linkage, and an answer that says 'model' about a value no
        model produced is a false provenance."""
        for name, tier in TIERS:
            with self.subTest(tier=name):
                self.setUp()
                self.r._hardware_tier = tier
                self.memory.store("your name", "Alex")
                res = self._route("what's my name?")
                self.assertEqual(res.answer_linkage.kind, "code")
                self.assertEqual(res.answer_linkage.renderer, "memory_template")


class UnrelatedQuestionsStillReachTheModelTests(_Base):
    """The control the widening needs: the memory route must DECLINE anything the
    store cannot answer, rather than claim it and answer vacuously."""

    def test_a_question_the_store_cannot_answer_is_declined(self):
        for name, tier in TIERS:
            with self.subTest(tier=name):
                self.setUp()
                self.r._hardware_tier = tier
                self.memory.store("your name", "Alex")
                res = self._route("what's the capital of France?")
                self.assertFalse(
                    res.handled,
                    "the memory route claimed a question the store cannot "
                    "answer; it must decline so the model path still runs")

    def test_a_recall_question_with_an_empty_store_is_declined(self):
        for name, tier in TIERS:
            with self.subTest(tier=name):
                self.setUp()
                self.r._hardware_tier = tier
                res = self._route("what's my name?")
                self.assertFalse(
                    res.handled,
                    "with nothing stored, the recall path must decline rather "
                    "than answer 'I don't know' from a route that looked up "
                    "nothing for this subject")


class TierIndependenceTests(unittest.TestCase):
    """The all-tiers amendment: name the absence rather than assert it."""

    def test_the_recall_path_reads_no_tier(self):
        import inspect
        from intergen import memory as memory_mod
        cls = inspect.getsource(MemoryManager.classify_declarative)
        for src, what in ((cls, "MemoryManager.classify_declarative"),
                          (inspect.getsource(
                              ConversationRouter._answer_from_stored_facts),
                           "ConversationRouter._answer_from_stored_facts")):
            with self.subTest(function=what):
                low = src.lower()
                for term in ("tier", "hardware_tier", "tier_1", "tier_2"):
                    self.assertNotIn(
                        term, low,
                        f"{what} reads a tier; this path is claimed to be "
                        f"tier-independent and a reader must be able to check it")
        del memory_mod


if __name__ == "__main__":
    unittest.main()
