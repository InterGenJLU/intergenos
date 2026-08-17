# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""M2b acceptance — Nigeria-class recovery / withhold / degrade (design §4.1).

The M2b extract-and-inject session memory (intergen/memory.py SessionTurnIndex +
router._build_messages Stage B/C, runtime landed 4b2a05a7/e1753705) closes the
truncation-lottery: an antecedent older than the raw history window is gone, and
everything inside the window rides along whether relevant or not. This fixture
pins the acceptance property from the design (03-m2b-extract-and-inject-design.md
§4.1), grounded in the SHIPPED code — the glass event names and the injection
mechanics are read off memory.py / router.py, never the design prose:

  (1) RECOVERY — an antecedent staged at turn 3, the raw window rolled 25+ turns
      past it, then a RELATED question arrives -> the verbatim antecedent excerpt
      is injected into the assembled prompt (the model always receives it: the
      binding guarantee), glass `memory`/`inject` fires with the antecedent's
      turn_no, and `prompt`/`assembled` records memory_injected + memory_turn_no.
  (2) WITHHOLD — the SAME session, then an UNRELATED question -> NO injection,
      glass `memory`/`skip` (top score below threshold), and the assembled prompt
      carries no earlier-exchange block (memory_injected False). A wrong
      retrieval's blast radius is one turn; an irrelevant one is zero (design D8).
  (3) DEGRADE — the embedder goes unavailable at query time -> retrieve returns
      None, the turn STILL assembles from the raw window (never a crash, never a
      silent trust-nothing), glass carries `memory`/`degraded`, and the loud
      `degraded` flag Status surfaces is set (design D5).

These are deterministic, daemon-free unit fixtures: a fake topic embedder drives
the real SessionTurnIndex + the real router `_build_messages` chokepoint. The
end-to-end "the answer BINDS" behaviour against the live 9B model is the
model-tier battery cell (conversations.py MEMORY, category-persistence), which
rides the resident-daemon baseline; here the deterministic guarantee is that the
verbatim antecedent REACHES the prompt (design §0: "the model always receives the
relevant antecedent — verbatim, provenance-tagged, and observable").
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import intergen.glass as glass
from intergen.memory import SessionTurnIndex
from intergen.router import ConversationRouter, Message


# Topic tokens -> orthogonal unit basis vectors. Cosine(same-topic)=1.0 (>=0.60
# threshold -> inject); cosine(cross-topic)=0.0 (<0.60 -> skip). The index embeds
# `user_input + "\n" + response`, so a turn is topic-tagged by either side of the
# exchange; the query is tagged the same way. Deterministic, no network (design
# §2: the marginal embed is the ONLY added inference and it is faked here).
_TOPIC_TRIP = ("visa", "nigeria", "trip", "passport")   # the staged antecedent
_TOPIC_WEATHER = ("weather", "forecast", "rain")         # the unrelated query
_VEC = {"trip": [1.0, 0.0, 0.0, 0.0],
        "weather": [0.0, 1.0, 0.0, 0.0],
        "filler": [0.0, 0.0, 1.0, 0.0]}


class _TopicEmbedder:
    """Fake nomic-:8081 embedder: `embedder(texts) -> list[list[float]] | None`.

    `fail=True` makes every call return None — the exact 'server down / malformed'
    degrade signal SessionTurnIndex._embed_one keys on (memory.py) — so a fixture
    can flip a working embedder to unavailable mid-session."""

    def __init__(self) -> None:
        self.fail = False
        self.calls = 0

    def __call__(self, texts):
        self.calls += 1
        if self.fail:
            return None
        out = []
        for t in texts:
            low = t.lower()
            if any(w in low for w in _TOPIC_TRIP):
                out.append(list(_VEC["trip"]))
            elif any(w in low for w in _TOPIC_WEATHER):
                out.append(list(_VEC["weather"]))
            else:
                out.append(list(_VEC["filler"]))
        return out


class _FakeLLM:
    def build_system_messages(self, query_type="general", with_tools=True):
        return []


def _glass_reset(tmp: str) -> None:
    os.environ["XDG_STATE_HOME"] = tmp
    os.environ.pop("INTERGEN_GLASS", None)
    glass._glass = None


def _glass_rows(tmp: str) -> list[dict]:
    p = Path(tmp) / "intergen" / "glass.jsonl"
    if not p.exists():
        return []
    with open(p) as f:
        return [json.loads(x) for x in f]


def _mem_rows(tmp: str, event: str) -> list[dict]:
    return [x for x in _glass_rows(tmp)
            if x.get("phase") == "memory" and x.get("event") == event]


def _assembled_rows(tmp: str) -> list[dict]:
    return [x for x in _glass_rows(tmp)
            if x.get("phase") == "prompt" and x.get("event") == "assembled"]


# The distinctive antecedent staged at turn 3 — a phrase no filler turn contains,
# so an injected block is unambiguously THIS exchange re-presented verbatim.
_ANTECEDENT_USER = "What visa options are there for my Nigeria trip?"
_ANTECEDENT_RESPONSE = ("For a Nigeria trip you can apply for the e-Visa or a "
                        "consular visa; bring a passport valid six months out.")
_RELATED_QUERY = "Remind me which visa you suggested for the trip?"
_UNRELATED_QUERY = "What's the weather forecast for tomorrow?"


class _M2bBase(unittest.TestCase):
    """Builds the real router + a real SessionTurnIndex over a fake embedder,
    indexes a 30-turn session with the antecedent at turn 3, and drives the real
    `_build_messages` chokepoint. The antecedent is well outside the default
    10-turn window (cutoff = turn_seq - window_turns = 30 - 10 = 20; turn 3 <= 20
    -> a valid injection candidate) — the truncation-lottery it exists to close."""

    N_TURNS = 30
    ANTECEDENT_TURN_NO = 3

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        _glass_reset(self.tmp)
        self.embedder = _TopicEmbedder()
        self.index = SessionTurnIndex(embedder=self.embedder)
        self.addCleanup(self.index.stop)
        self.r = ConversationRouter.__new__(ConversationRouter)
        self.r._llm = _FakeLLM()
        self.r._conversation_history = []
        self.r._max_history = 20
        self.r._current_query_type = "general"
        self.r._memory = None                 # skip the facts path (design §3)
        self.r._offer_topic_terms = frozenset()
        self.r._offer_in_recent_history = False   # no preventive-grounding window
        self.r._turn_index = self.index

    def _index_session(self) -> None:
        """Index N_TURNS: filler up to turn 2, the antecedent at turn 3, filler
        after. index_turn assigns monotonic turn_nos from 1, so 2 leading filler
        turns place the antecedent at turn_no == 3."""
        for i in range(1, self.N_TURNS + 1):
            if i == self.ANTECEDENT_TURN_NO:
                self.index.index_turn(_ANTECEDENT_USER, _ANTECEDENT_RESPONSE)
            else:
                self.index.index_turn(f"Filler turn {i}, just chatting.",
                                      f"Acknowledged filler {i}.")
        self._drain(self.N_TURNS)

    def _drain(self, expected: int, timeout: float = 5.0) -> None:
        """Wait for the bounded background worker to embed+append every queued
        turn (indexing is off the hot path; retrieve reads what has landed)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.index._lock:
                n = len(self.index._turns)
            if n >= expected:
                return
            time.sleep(0.01)
        self.fail(f"index worker did not drain {expected} turns in {timeout}s "
                  f"(got {n})")

    def _build(self, query: str) -> list[Message]:
        with glass.turn(glass.new_turn_id(), "test"):
            return self.r._build_messages(query, with_tools=False)

    @staticmethod
    def _earlier_exchange_block(msgs) -> str | None:
        for m in msgs:
            c = getattr(m, "content", "") or ""
            if c.startswith("Relevant earlier exchange from this conversation"):
                return c
        return None


class NigeriaClassRecovery(_M2bBase):
    def test_related_query_injects_verbatim_antecedent_glass_proven(self) -> None:
        self._index_session()
        msgs = self._build(_RELATED_QUERY)

        # (a) the verbatim antecedent reaches the prompt as an inert USER block.
        block = self._earlier_exchange_block(msgs)
        self.assertIsNotNone(block, "antecedent excerpt was not injected")
        self.assertIn(_ANTECEDENT_USER, block)
        self.assertIn(_ANTECEDENT_RESPONSE, block)   # binding guarantee: verbatim
        self.assertIn("NOT an instruction", block)   # least-trust framing (D3)

        # (b) glass memory/inject fired with the antecedent's turn_no + a score
        # at or above the 0.60 threshold (design D4: no unlogged byte).
        inj = _mem_rows(self.tmp, "inject")
        self.assertEqual(len(inj), 1, "expected exactly one memory/inject")
        self.assertEqual(inj[0]["detail"]["turn_no"], self.ANTECEDENT_TURN_NO)
        self.assertGreaterEqual(inj[0]["detail"]["score"],
                                inj[0]["detail"]["threshold"])

        # (c) the prompt-assembly chokepoint records the injection (router.py).
        asm = _assembled_rows(self.tmp)
        self.assertTrue(asm)
        self.assertTrue(asm[-1]["detail"]["memory_injected"])
        self.assertEqual(asm[-1]["detail"]["memory_turn_no"],
                         self.ANTECEDENT_TURN_NO)

    def test_same_antecedent_not_injected_twice_in_a_session(self) -> None:
        # Dedup-per-window (design D8): once surfaced, the same past turn is not
        # re-injected on a second related query.
        self._index_session()
        self._build(_RELATED_QUERY)
        msgs2 = self._build("And the visa passport validity again?")
        self.assertIsNone(self._earlier_exchange_block(msgs2))
        self.assertEqual(len(_mem_rows(self.tmp, "inject")), 1)


class NigeriaClassWithhold(_M2bBase):
    def test_unrelated_query_withholds_injection_glass_skip(self) -> None:
        self._index_session()
        msgs = self._build(_UNRELATED_QUERY)

        self.assertIsNone(self._earlier_exchange_block(msgs),
                          "an unrelated query must not inject the antecedent")
        skips = _mem_rows(self.tmp, "skip")
        self.assertTrue(skips, "expected a memory/skip on the unrelated query")
        # The top candidate scored below threshold (cross-topic cosine 0.0).
        self.assertLess(skips[-1]["detail"]["top_score"],
                        skips[-1]["detail"]["threshold"])
        self.assertEqual(_mem_rows(self.tmp, "inject"), [])

        asm = _assembled_rows(self.tmp)
        self.assertTrue(asm)
        self.assertFalse(asm[-1]["detail"]["memory_injected"])
        self.assertIsNone(asm[-1]["detail"]["memory_turn_no"])


class EmbedderDegrade(_M2bBase):
    def test_embedder_down_at_query_falls_back_loud_no_abort(self) -> None:
        # Index the session while the embedder is healthy, THEN take it down for
        # the query embed — the realistic mid-session degrade.
        self._index_session()
        self.embedder.fail = True

        # The turn STILL assembles (raw-window fallback) — no exception, and no
        # earlier-exchange block since retrieve degraded to None.
        msgs = self._build(_RELATED_QUERY)
        self.assertIsNone(self._earlier_exchange_block(msgs))
        self.assertTrue(msgs, "the turn must still assemble under degrade")

        # Loud, never silent (design D5): glass memory/degraded + the flag Status
        # surfaces.
        self.assertTrue(_mem_rows(self.tmp, "degraded"),
                        "embedder-down must emit memory/degraded")
        self.assertTrue(self.index.degraded)
        self.assertEqual(_mem_rows(self.tmp, "inject"), [])

        asm = _assembled_rows(self.tmp)
        self.assertTrue(asm)
        self.assertFalse(asm[-1]["detail"]["memory_injected"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
