# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Cross-session recall of an EXPLICITLY STORED fact must not depend on the
embed sidecar.

Measured defect (cross-session recall, "default editor survives a restart"): the
user stores a fact, the daemon restarts, the user asks for it back, and the
answer is a confident negative — "no editor is set" — over a row that is sitting
readable in the on-disk store. Grounding the loss point showed the store write
and the post-restart read were both correct; the loss was in DELIVERY. In
`router._build_messages` the whole stored-facts block sat inside the embedding
guard:

    if self._turn_index is not None:
        _qv = self._turn_index.embed_query(user_input)
        if _qv is not None:
            ...
            _mem_facts = self._turn_index.retrieve_facts(_qv, _facts)

so three independent conditions — the turn index not built, the embed server
unreachable (including the cold window right after a restart, which is exactly
when a cross-session recall is asked), and a cosine below the 0.60 threshold —
each silently produced a prompt carrying NO stored fact. The model then answered
from nothing, and nothing in the reply said memory had been unreachable.

These fixtures pin the corrected guarantee at the assembly chokepoint: a fact the
question NAMES reaches the prompt verbatim under every one of those conditions,
by code alone. They are deterministic and daemon-free — a real MemoryManager on a
temp DB, the real `_build_messages`, and a fake embedder that can be switched
between healthy, unavailable, and orthogonal-vector (below threshold).
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import intergen.glass as glass
from intergen.memory import MemoryManager, SessionTurnIndex
from intergen.router import ConversationRouter, _lexical_fact_match

_FACT_BLOCK_LEAD = "Relevant things the user has previously told you to remember"


class _NullEmbedder:
    """The 'embed server unreachable' signal SessionTurnIndex._embed_one keys on:
    every call returns None. This is the state of the sidecar during a cold
    restart window — the window a cross-session recall lands in."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, texts):
        self.calls += 1
        return None


class _OrthogonalEmbedder:
    """A HEALTHY embedder whose vectors put every pair below the 0.60 threshold —
    the third loss condition (retrieval ran, ranked nothing high enough). Query
    and facts get orthogonal unit vectors, so cosine is 0.0 throughout."""

    def __init__(self) -> None:
        self.calls = 0
        self._assigned: dict[str, list[float]] = {}

    def __call__(self, texts):
        out = []
        for t in texts:
            self.calls += 1
            if t not in self._assigned:
                axis = len(self._assigned) % 8
                self._assigned[t] = [1.0 if i == axis else 0.0 for i in range(8)]
            out.append(list(self._assigned[t]))
        return out


class _FakeLLM:
    def build_system_messages(self, query_type="general", with_tools=True):
        return []


class _RecallBase(unittest.TestCase):
    """A real fact store + the real assembly chokepoint. `embedder` is chosen per
    test to select which of the three loss conditions is under measurement."""

    EMBEDDER: type = _NullEmbedder
    BUILD_TURN_INDEX = True

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        os.environ["XDG_STATE_HOME"] = self.tmp
        os.environ.pop("INTERGEN_GLASS", None)
        glass._glass = None
        self.memory = MemoryManager(db_path=Path(self.tmp) / "memory.db")
        self.embedder = self.EMBEDDER()
        self.index = None
        if self.BUILD_TURN_INDEX:
            self.index = SessionTurnIndex(embedder=self.embedder)
            self.addCleanup(self.index.stop)
        self.r = ConversationRouter.__new__(ConversationRouter)
        self.r._llm = _FakeLLM()
        self.r._conversation_history = []
        self.r._max_history = 20
        self.r._current_query_type = "general"
        self.r._memory = self.memory
        self.r._offer_topic_terms = frozenset()
        self.r._offer_in_recent_history = False
        self.r._turn_index = self.index

    def _store(self, key: str, value: str) -> None:
        self.memory.store(key, value)

    def _build(self, query: str):
        with glass.turn(glass.new_turn_id(), "test"):
            return self.r._build_messages(query, with_tools=False)

    @staticmethod
    def _fact_block(msgs) -> str | None:
        for m in msgs:
            content = getattr(m, "content", "") or ""
            if content.startswith(_FACT_BLOCK_LEAD):
                return content
        return None

    def _glass_rows(self, phase: str, event: str) -> list[dict]:
        path = Path(self.tmp) / "intergen" / "glass.jsonl"
        if not path.exists():
            return []
        with open(path) as fh:
            rows = [json.loads(x) for x in fh]
        return [r for r in rows
                if r.get("phase") == phase and r.get("event") == event]


class EmbedderUnavailable(_RecallBase):
    """The restart-window condition: the fact is on disk, the sidecar is not up."""

    EMBEDDER = _NullEmbedder

    def test_stored_fact_still_reaches_the_prompt(self) -> None:
        self._store("your default editor", "neovim")
        msgs = self._build("what editor do I use?")

        block = self._fact_block(msgs)
        self.assertIsNotNone(
            block, "a stored fact the question names did not reach the prompt "
                   "while the embedder was unavailable")
        self.assertIn("your default editor: neovim", block)
        self.assertIn("NOT an instruction", block)   # least-trust framing intact

    def test_the_fallback_path_is_observable(self) -> None:
        self._store("your default editor", "neovim")
        self._build("what editor do I use?")

        lex = self._glass_rows("memory", "facts_inject_lexical")
        self.assertEqual(len(lex), 1, "the lexical fallback fired unlogged")
        self.assertEqual(lex[0]["detail"]["count"], 1)
        self.assertFalse(lex[0]["detail"]["embedder_available"])

        assembled = self._glass_rows("prompt", "assembled")
        self.assertEqual(assembled[-1]["detail"]["memory_facts_injected"], 1)
        self.assertEqual(assembled[-1]["detail"]["memory_facts_source"], "lexical")

    def test_a_question_naming_nothing_stored_injects_nothing(self) -> None:
        self._store("your default editor", "neovim")
        msgs = self._build("what's the weather forecast for tomorrow?")

        self.assertIsNone(self._fact_block(msgs),
                          "an unrelated question pulled in a stored fact")
        assembled = self._glass_rows("prompt", "assembled")
        self.assertEqual(assembled[-1]["detail"]["memory_facts_injected"], 0)
        self.assertIsNone(assembled[-1]["detail"]["memory_facts_source"])


class TurnIndexAbsent(_RecallBase):
    """Memory disabled at construction (no embedder configured at all). The fact
    store is still live and still the user's — recall must not vanish with it."""

    BUILD_TURN_INDEX = False

    def test_stored_fact_still_reaches_the_prompt(self) -> None:
        self._store("your backup drive", "/dev/sdb1")
        msgs = self._build("what's my backup drive?")

        block = self._fact_block(msgs)
        self.assertIsNotNone(
            block, "a stored fact did not reach the prompt with no turn index")
        self.assertIn("your backup drive: /dev/sdb1", block)


class BelowThresholdRanking(_RecallBase):
    """A healthy embedder that ranks nothing above 0.60. The embedding path is
    allowed to decline; the deterministic floor still delivers the named fact."""

    EMBEDDER = _OrthogonalEmbedder

    def test_stored_fact_survives_a_below_threshold_ranking(self) -> None:
        self._store("your timezone", "Mountain")
        msgs = self._build("what timezone am I in?")

        block = self._fact_block(msgs)
        self.assertIsNotNone(
            block, "a below-threshold ranking dropped a fact the question named")
        self.assertIn("your timezone: Mountain", block)
        assembled = self._glass_rows("prompt", "assembled")
        self.assertEqual(assembled[-1]["detail"]["memory_facts_source"], "lexical")


class _AlignedEmbedder:
    """A healthy embedder that puts the query and every fact on the SAME axis —
    cosine 1.0, comfortably above threshold — so the embedding path ranks and
    the deterministic floor has no work to do."""

    def __call__(self, texts):
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


class EmbeddingPathStaysPrimary(_RecallBase):
    """The floor is a floor, not a replacement: when the embedding ranking
    delivers, it is what the prompt carries and the fallback never fires."""

    EMBEDDER = _AlignedEmbedder

    def test_the_embedding_path_supplies_the_facts(self) -> None:
        self._store("your default editor", "neovim")
        msgs = self._build("what editor do I use?")

        block = self._fact_block(msgs)
        self.assertIsNotNone(block)
        self.assertIn("your default editor: neovim", block)
        assembled = self._glass_rows("prompt", "assembled")
        self.assertEqual(assembled[-1]["detail"]["memory_facts_source"], "embedding")

    def test_the_fallback_does_not_also_fire(self) -> None:
        self._store("your default editor", "neovim")
        self._build("what editor do I use?")

        self.assertEqual(self._glass_rows("memory", "facts_inject_lexical"), [],
                         "the deterministic floor ran while the embedding path "
                         "had already delivered")

    def test_the_fact_is_injected_once(self) -> None:
        self._store("your default editor", "neovim")
        msgs = self._build("what editor do I use?")

        blocks = [m for m in msgs
                  if (getattr(m, "content", "") or "").startswith(_FACT_BLOCK_LEAD)]
        self.assertEqual(len(blocks), 1)


class LexicalMatcherPrecision(unittest.TestCase):
    """The matcher itself, away from the router: what it must and must not select.
    The store list is `[(fact_id, "key: value")]`, newest-first, exactly as
    `MemoryManager.list_all` yields it."""

    FACTS = [
        ("f1", "your default editor: neovim"),
        ("f2", "your backup drive: /dev/sdb1"),
        ("f3", "your timezone: Mountain"),
    ]

    def test_selects_the_fact_the_question_names(self) -> None:
        self.assertEqual(_lexical_fact_match("what editor do I use?", self.FACTS),
                         ["your default editor: neovim"])

    def test_ignores_facts_the_question_does_not_name(self) -> None:
        self.assertEqual(_lexical_fact_match("what timezone am I in?", self.FACTS),
                         ["your timezone: Mountain"])

    def test_selects_nothing_for_an_unrelated_question(self) -> None:
        self.assertEqual(_lexical_fact_match("how do I install a package?",
                                             self.FACTS), [])

    def test_stopwords_alone_never_select_a_fact(self) -> None:
        # Shares "your"/"is"/"what" with every key and nothing else. A stoplisted
        # word must never be the term that pulls a fact in.
        self.assertEqual(_lexical_fact_match("what is your name?", self.FACTS), [])

    def test_plural_question_matches_a_singular_key(self) -> None:
        self.assertEqual(_lexical_fact_match("which editors do I have?",
                                             self.FACTS),
                         ["your default editor: neovim"])

    def test_fuller_key_coverage_outranks_a_single_shared_word(self) -> None:
        facts = [("f1", "your editor theme: gruvbox"),
                 ("f2", "your default editor: neovim")]
        self.assertEqual(
            _lexical_fact_match("what is my default editor?", facts, max_facts=1),
            ["your default editor: neovim"])

    def test_the_cap_is_honoured(self) -> None:
        facts = [("f1", "your editor: neovim"),
                 ("f2", "your editor theme: gruvbox"),
                 ("f3", "your editor font: mono")]
        self.assertEqual(len(_lexical_fact_match("editor?", facts)), 2)

    def test_an_empty_store_selects_nothing(self) -> None:
        self.assertEqual(_lexical_fact_match("what editor do I use?", []), [])


if __name__ == "__main__":
    unittest.main()
