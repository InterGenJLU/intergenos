# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""What the session index hands the embedder is bounded by the server's own
reported context, and a longer turn stays retrievable by any part of itself.

THE DEFECT THIS PINS. SessionTurnIndex._drain (intergen/memory.py) embeds a
completed exchange as ONE text: `user_input + "\\n" + response`, handed straight
to the embedding client with no size bound at that layer. Nothing in the index
consults how much the embedding server can actually take. The only bound
anywhere is LlamaManager._fit_to_context (intergen/llama_manager.py), which
runs INSIDE the client, shortens the text to the server's context and DROPS THE
TAIL — logging that it did. The consequences are two, and they are distinct:

  (1) The index hands over a text it has no reason to believe the server can
      process. Whether it is shortened, refused, or answered is decided
      somewhere else, by a component the index cannot see.
  (2) Everything past the context is not merely un-embedded, it is
      UNRETRIEVABLE. A long exchange is represented by a vector built from its
      opening only, so a later question about anything the tail said scores
      against text that no longer mentions it. The turn is in the index and
      cannot be found — the exact truncation-lottery this index exists to close,
      reintroduced one layer down.

WHAT THE FIX MUST DO. Derive the budget from the SERVER'S REPORTED number (the
embedding LlamaManager's `context_size`, reached through the bound `.embed`
method the router is wired with — dbus_daemon.py passes
`self._embed_llama.embed`), split an over-budget exchange into chunks that each
fit, embed every chunk, and score a turn by its BEST chunk. The character
budget is the same one-sided-sound pre-filter _fit_to_context already uses: a
token is at least one character, so a text shorter than the context IN
CHARACTERS cannot exceed it in tokens.

WHAT THE FIX MUST NOT DO. The stored turn text stays VERBATIM (design D2, the
invariant the class docstring states as "no truncation of turn content
anywhere"). Chunking governs what is EMBEDDED; what is stored and later quoted
back to the user is untouched. `test_the_stored_turn_stays_verbatim` is the
control on that, and it must pass both before and after the fix.

THE FAKE MODELS THE REAL SERVER, NOT A CONVENIENT ONE. `_BoundedEmbedder` is
shaped like the thing the index is really wired to: a manager object exposing
`context_size` whose BOUND `.embed` method is the callable handed in. It
records every text it is asked to embed, and — like the real path — derives its
vector from the text TRUNCATED to the context. That truncation is what makes
the tail unretrievable at base; a fake that embedded the whole string would
report a green that the shipped code does not earn.
"""

from __future__ import annotations

import threading
import time
import unittest

from intergen.memory import SessionTurnIndex


# Topic markers -> orthogonal unit basis vectors, the same deterministic scheme
# the other M2b fixtures use. cosine(same)=1.0 (>= the 0.60 threshold -> a hit),
# cosine(different)=0.0 (-> a miss). No network, no real embedder: the marginal
# embed is the only added inference in this path and it is faked here.
_VEC = {
    "aurora": [1.0, 0.0, 0.0, 0.0],
    "basalt": [0.0, 1.0, 0.0, 0.0],
    "cinder": [0.0, 0.0, 1.0, 0.0],
    "filler": [0.0, 0.0, 0.0, 1.0],
}


class _BoundedEmbedder:
    """Stands in for the embedding LlamaManager the router is wired to.

    The index is given `manager.embed` — a BOUND method — exactly as
    dbus_daemon.py wires `self._embed_llama.embed`. So the object reached
    through that callable is the only place the server's reported context is
    available, and this fake exposes it under the same name the real manager
    does (`context_size`).

    `embed` records each text VERBATIM as it was handed over (that recording is
    what the bound assertion reads), then derives the vector from the text
    truncated to `context_size` characters — modelling _fit_to_context's
    drop-the-tail behaviour inside the real client.
    """

    def __init__(self, context_size: int) -> None:
        self.context_size = context_size
        self.handed: list[str] = []
        self._lock = threading.Lock()

    def embed(self, texts):
        out = []
        with self._lock:
            for text in texts:
                self.handed.append(text)
                # What the server would actually SEE, tail dropped.
                seen = text[: self.context_size].lower()
                vec = [0.0, 0.0, 0.0, 0.0]
                for marker, basis in _VEC.items():
                    if marker in seen:
                        vec = [a + b for a, b in zip(vec, basis)]
                out.append(vec)
        return out

    @property
    def longest_handed(self) -> int:
        with self._lock:
            return max((len(t) for t in self.handed), default=0)


def _filler(n: int) -> str:
    """n characters that carry the 'filler' marker and nothing else."""
    block = "filler words that say nothing in particular. "
    return (block * (n // len(block) + 1))[:n]


class BoundedEmbedInputTests(unittest.TestCase):
    """The index never hands the embedder more than the server reported."""

    CONTEXT = 512

    def setUp(self) -> None:
        self.embedder = _BoundedEmbedder(context_size=self.CONTEXT)
        # window_turns=1 so a turn leaves the raw window immediately and is a
        # retrieval candidate on the next turn; retrieval mechanics are not what
        # this fixture is measuring.
        self.index = SessionTurnIndex(self.embedder.embed, window_turns=1)
        self.addCleanup(self._stop)

    def _stop(self) -> None:
        try:
            self.index.clear()
        except Exception:
            pass

    def _await_indexed(self, expected: int, timeout: float = 5.0) -> None:
        """Wait for the bounded worker to drain, or fail naming what it reached."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.index._lock:
                if len(self.index._turns) >= expected:
                    return
            time.sleep(0.01)
        with self.index._lock:
            got = len(self.index._turns)
        self.fail(f"the index worker drained {got} of {expected} turns in "
                  f"{timeout}s (degraded={self.index.degraded})")

    # ── The bound itself ────────────────────────────────────────────────────

    def test_no_text_handed_to_the_embedder_exceeds_the_reported_context(self):
        """An over-context exchange must reach the embedder in pieces that fit.

        RED AT BASE: _drain hands `user_input + "\\n" + response` as one string,
        so the longest handed text is the whole exchange — many times the 512
        characters this server reported.
        """
        user = "Tell me about " + _filler(4000)
        response = "Certainly. " + _filler(4000)
        self.index.index_turn(user, response)
        self._await_indexed(1)

        self.assertGreater(self.embedder.handed, [],
                           "the embedder was never called — the fixture proved "
                           "nothing about what it was handed")
        self.assertLessEqual(
            self.embedder.longest_handed, self.CONTEXT,
            f"the index handed the embedder a text of "
            f"{self.embedder.longest_handed} characters while the server "
            f"reported a context of {self.CONTEXT}; every text must fit the "
            f"number the server itself gave")

    def test_the_bound_tracks_the_servers_number_not_a_constant(self):
        """Halve the server's reported context and the bound must halve too.

        A hard-coded limit that happened to sit under 512 would pass the test
        above and fail this one. The budget has to come from the server.
        """
        small = _BoundedEmbedder(context_size=128)
        index = SessionTurnIndex(small.embed, window_turns=1)
        self.addCleanup(lambda: index.clear())
        index.index_turn("Tell me about " + _filler(4000), "Sure. " + _filler(4000))

        deadline = time.time() + 5.0
        while time.time() < deadline:
            with index._lock:
                if index._turns:
                    break
            time.sleep(0.01)

        self.assertLessEqual(
            small.longest_handed, 128,
            f"with the server reporting a 128-character context the index still "
            f"handed it {small.longest_handed} characters")

    def test_a_short_turn_is_handed_over_whole(self):
        """Control. An exchange that already fits is not chunked or altered.

        This passes at base and must still pass on the branch: the fix bounds
        what does not fit, it does not reshape what does.
        """
        user = "What is aurora?"
        response = "Aurora is light in the sky."
        self.index.index_turn(user, response)
        self._await_indexed(1)

        self.assertEqual(
            self.embedder.handed, [f"{user}\n{response}"],
            "a short exchange must reach the embedder exactly once, whole and "
            "unaltered")

    # ── Retrievability, which is the point of the bound ─────────────────────

    def test_a_long_turn_is_retrievable_by_content_in_its_tail(self):
        """The tail of a long exchange must still be findable.

        RED AT BASE: the single vector is built from the first 512 characters,
        which mention only filler. The word the later question asks about lives
        past that point, so the turn scores 0.0 and retrieve() returns None —
        the turn is in the index and cannot be found.
        """
        user = "Here are my notes: " + _filler(3000)
        response = _filler(3000) + " and the conclusion concerns basalt."
        self.index.index_turn(user, response)
        self._await_indexed(1)
        # Roll the raw window so the staged turn is a candidate.
        self.index.index_turn("Something else entirely.", "Noted.")
        self._await_indexed(2)

        hit = self.index.retrieve("what did we conclude about basalt?")

        self.assertIsNotNone(
            hit,
            "the exchange whose tail discussed basalt was not retrievable by a "
            "question about basalt — everything past the server's context was "
            "dropped, so the turn is indexed but unfindable")
        self.assertEqual(hit.turn_no, 1)

    def test_a_long_turn_is_still_retrievable_by_content_in_its_head(self):
        """Chunking must not cost the head what it gives the tail."""
        user = "The subject is aurora. " + _filler(3000)
        response = _filler(3000) + " end of notes."
        self.index.index_turn(user, response)
        self._await_indexed(1)
        self.index.index_turn("Something else entirely.", "Noted.")
        self._await_indexed(2)

        hit = self.index.retrieve("tell me about aurora")

        self.assertIsNotNone(
            hit, "the exchange whose opening discussed aurora was not "
                 "retrievable by a question about aurora")
        self.assertEqual(hit.turn_no, 1)

    def test_an_unrelated_question_still_misses_a_long_turn(self):
        """The negative. Chunking must not turn a long turn into a match-all.

        More vectors per turn means more chances to clear the threshold. If a
        long exchange about basalt starts answering questions about cinder, the
        fix has bought retrievability with false injection — a worse defect than
        the one it closes.
        """
        user = "Here are my notes: " + _filler(3000)
        response = _filler(3000) + " and the conclusion concerns basalt."
        self.index.index_turn(user, response)
        self._await_indexed(1)
        self.index.index_turn("Something else entirely.", "Noted.")
        self._await_indexed(2)

        self.assertIsNone(
            self.index.retrieve("what did we say about cinder?"),
            "a long exchange that never mentioned cinder was injected for a "
            "question about cinder")

    # ── The invariant the fix must not break ────────────────────────────────

    def test_the_stored_turn_stays_verbatim(self):
        """Design D2. Chunking governs the EMBEDDING, never the stored text.

        Passes at base and must still pass on the branch. What is quoted back to
        the user is the exchange as it was actually said, at any length.
        """
        user = "Here are my notes: " + _filler(3000)
        response = _filler(3000) + " and the conclusion concerns basalt."
        self.index.index_turn(user, response)
        self._await_indexed(1)

        with self.index._lock:
            stored = self.index._turns[0]
        self.assertEqual(stored.user_input, user,
                         "the stored user input was altered")
        self.assertEqual(stored.response, response,
                         "the stored response was altered")


if __name__ == "__main__":
    unittest.main()
