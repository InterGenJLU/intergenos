# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The Status degraded flag must be SELF-HEALING, not a latch.

Reported against r109: `intergen status` printed the loud
"MEMORY DEGRADED — session recall is OFFLINE" banner while a direct probe of the
embedder returned HTTP 200 in milliseconds, the journal showed successful
/v1/embeddings calls, and the same output still listed "[+] memory". One cold
embed timeout during model warm-up set the flag and nothing on the per-turn path
ever cleared it.

The class under test is the honesty of the flag in BOTH directions. A quieter
banner would be the wrong fix, so every test here pairs a recovery assertion
with an assertion that a genuine outage is still reported loudly.
"""
import unittest

from intergen.memory import SessionTurnIndex


class _Embedder:
    """Embedder stub whose availability can be flipped mid-test.

    Returns a fixed 4-D vector when up, and models the two real failure shapes:
    a None return (server down / malformed) and a raised exception (transient
    connection error), which _embed_one is contracted to treat identically.
    """

    def __init__(self, up=True, mode="none"):
        self.up = up
        self.mode = mode
        self.calls = 0

    def __call__(self, texts):
        self.calls += 1
        if self.up:
            return [[0.5, 0.5, 0.5, 0.5] for _ in texts]
        if self.mode == "raise":
            raise ConnectionError("embedder unreachable")
        return None


class DegradedFlagSelfHealTests(unittest.TestCase):

    def _index(self, embedder):
        idx = SessionTurnIndex(embedder)
        self.addCleanup(idx.stop)
        return idx

    # ---- the reported defect -------------------------------------------

    def test_cold_start_timeout_then_success_clears_the_flag(self):
        """The exact r109 sequence: one failed embed, then a healthy one."""
        emb = _Embedder(up=False)
        idx = self._index(emb)

        self.assertIsNone(idx.embed_query("first turn during warm-up"))
        self.assertTrue(idx.degraded, "a failed embed must raise the flag loudly")

        emb.up = True
        self.assertIsNotNone(idx.embed_query("a later turn, embedder healthy"))
        self.assertFalse(
            idx.degraded,
            "a successful embed is live proof the embedder is back; the flag "
            "must clear rather than latch for the life of the daemon")

    def test_flag_stays_clear_across_further_healthy_turns(self):
        emb = _Embedder(up=False)
        idx = self._index(emb)
        idx.embed_query("warm-up failure")
        self.assertTrue(idx.degraded)

        emb.up = True
        for n in range(5):
            idx.embed_query(f"healthy turn {n}")
            self.assertFalse(idx.degraded)

    def test_a_raising_embedder_latches_then_also_self_heals(self):
        """A transient connection error degrades identically and recovers identically."""
        emb = _Embedder(up=False, mode="raise")
        idx = self._index(emb)
        self.assertIsNone(idx.embed_query("turn while the socket is refused"))
        self.assertTrue(idx.degraded)

        emb.up = True
        self.assertIsNotNone(idx.embed_query("turn after recovery"))
        self.assertFalse(idx.degraded)

    # ---- the other direction: never a quieter banner --------------------

    def test_genuine_outage_still_reports_degraded(self):
        """A real outage must still be loud — the fix is truthful state, not quiet state."""
        emb = _Embedder(up=True)
        idx = self._index(emb)
        idx.embed_query("healthy turn")
        self.assertFalse(idx.degraded)

        emb.up = False
        self.assertIsNone(idx.embed_query("turn during a real outage"))
        self.assertTrue(idx.degraded, "a genuine outage must never be masked")

    def test_recovery_then_relapse_reports_degraded_again(self):
        """Recovery must not make the flag sticky in the OTHER direction."""
        emb = _Embedder(up=False)
        idx = self._index(emb)
        idx.embed_query("fail")
        emb.up = True
        idx.embed_query("recover")
        self.assertFalse(idx.degraded)

        emb.up = False
        idx.embed_query("fail again")
        self.assertTrue(idx.degraded)

    def test_embedder_absent_entirely_is_degraded_and_stays_degraded(self):
        """No embedder configured is a real, permanent outage — nothing to heal."""
        idx = self._index(None)
        self.assertIsNone(idx.embed_query("any turn"))
        self.assertTrue(idx.degraded)
        self.assertIsNone(idx.embed_query("another turn"))
        self.assertTrue(idx.degraded)

    # ---- retrieve() owns the same asymmetry ------------------------------

    def test_retrieve_self_embed_success_clears_the_flag(self):
        """retrieve() sets the flag when it embeds and fails; it must clear when it succeeds.

        The retrieve path only reaches its own embed when candidates exist, so the
        index is seeded with enough turns to push one outside the raw window.
        """
        emb = _Embedder(up=True)
        idx = SessionTurnIndex(emb, window_turns=1)
        self.addCleanup(idx.stop)
        for n in range(4):
            idx.index_turn(f"user {n}", f"response {n}")
        idx.flush() if hasattr(idx, "flush") else None
        # Drive the worker to completion deterministically.
        import time
        deadline = time.time() + 5
        while time.time() < deadline and not idx._turns:
            time.sleep(0.02)

        with idx._lock:
            idx._degraded = True          # simulate the latched state
        idx.retrieve("a query the index must embed itself")
        self.assertFalse(
            idx.degraded,
            "retrieve() embedding successfully is the same live proof as the "
            "per-turn path and must clear the flag too")

    def test_retrieve_does_not_clear_on_a_handed_in_vector(self):
        """A caller-supplied vector is NOT evidence retrieve() reached the embedder."""
        emb = _Embedder(up=True)
        idx = SessionTurnIndex(emb, window_turns=1)
        self.addCleanup(idx.stop)
        with idx._lock:
            idx._degraded = True
        idx.retrieve("query", query_vector=[0.5, 0.5, 0.5, 0.5])
        self.assertTrue(
            idx.degraded,
            "reusing a vector proves nothing about the embedder's current "
            "reachability, so it must not clear a degraded flag")


if __name__ == "__main__":
    unittest.main()
