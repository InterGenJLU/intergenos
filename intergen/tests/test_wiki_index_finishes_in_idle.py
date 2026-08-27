# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An index that started degraded must finish while the daemon serves turns.

THE DEFECT THESE TESTS PIN DOWN
-------------------------------
``WikiRetrieval.resume_embedding()`` exists, is correct, is bounded, is
idempotent, and is covered by its own tests in
intergen/tests/test_wiki_startup_embedding.py — and **nothing in the shipped
product ever calls it**. At the tree this file arrives on, every reference to
the name outside wiki_retrieval.py itself is in a test file; the daemon, the
web server and the router do not mention it.

So the recovery path that exists on paper does not exist in practice. The
shipped consequence is exact and it is not hypothetical: the installed wiki is
2 116 passages from 87 pages, index construction is allowed 10 seconds
(``_STARTUP_EMBED_BUDGET_S``), and on a machine where the embedding server is
slow to come up — the greeter cold-boot collision, the provisioning window, a
first boot before the model is downloaded — that budget expires with the corpus
part-embedded. ``embeddings_ready`` stays False, ``retrieve()`` scores against
nothing and the wiki answers by keyword match for **the entire life of the
daemon**. The only cure a user has is a reboot, and a reboot that hits the same
cold-start window does not cure it either.

The intent layer already solved the same problem the other way round:
``SemanticMatcher.refresh_pending_intents`` has a caller
(``_start_embed_server_and_recover_intents``), so intents recover. Wiki
passages do not.

WHY THE EXISTING TESTS DO NOT CATCH IT
--------------------------------------
test_wiki_startup_embedding.py's RecoveryTests calls ``resume_embedding()``
**itself**, in a loop, from the test body. That proves the method works. It
cannot prove anybody calls it, and nobody does. A test that supplies the very
call whose absence is the defect will pass on a tree that has the defect —
which is what happened here.

WHAT THIS FILE ASSERTS
----------------------
  1. WIRED       — driving turns through the daemon's own turn path finishes an
                   index that started degraded, without the test ever calling
                   resume_embedding.
  2. BOUNDED     — the work runs off the turn, so a turn's own latency does not
                   grow, and one pass cannot run away.
  3. SLOT-AWARE  — the pass is not started while an embedding request is
                   already in flight, because the server ships --parallel 1 and
                   a second consumer simply waits out its timeout.
  4. QUIET       — once the index is complete the turn path stops asking, so a
                   healthy machine pays nothing per turn.

The embedding server stand-in models the NETWORK — one slot, a batch ceiling,
a per-input cost, and an availability flag. The retrieval object, the daemon
method under test and the turn path are the shipped ones.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from intergen import dbus_daemon as dd
from intergen.destructive_policy import OPERATOR_FINGERPRINT
from intergen.wiki_citations import WikiCitations
from intergen.wiki_retrieval import WikiRetrieval

_FPR = OPERATOR_FINGERPRINT


def _valid_status(primary_fpr: str = _FPR) -> str:
    return (f"[GNUPG:] NEWSIG\n[GNUPG:] VALIDSIG SUB 2026-07-10 0 4 0 1 8 00 "
            f"{primary_fpr}\n")


def _good_verify(sig_path: str, data: bytes) -> "tuple[int, str]":
    return 0, _valid_status()


class _SingleSlotEmbedder:
    """An embedding server with one slot, a batch ceiling and a per-input cost.

    Deliberately the same shape as the stand-in in
    test_wiki_startup_embedding.py: it stands in for the NETWORK, which is the
    only thing it replaces.
    """

    DIM = 8

    def __init__(self, capacity: "int | None" = None,
                 cost_per_input: float = 0.0, available: bool = True):
        self.capacity = capacity
        self.cost_per_input = cost_per_input
        self.available = available
        self.calls: list[int] = []
        self._lock = threading.Lock()

    def __call__(self, texts: "list[str]") -> "list[list[float]] | None":
        texts = list(texts)
        with self._lock:
            self.calls.append(len(texts))
            available = self.available
        if not available:
            return None
        if self.capacity is not None and len(texts) > self.capacity:
            return None
        if self.cost_per_input:
            time.sleep(self.cost_per_input * len(texts))
        return [[float(len(t) % 7)] * self.DIM for t in texts]

    @property
    def call_count(self) -> int:
        with self._lock:
            return len(self.calls)


def _corpus_html(word_count: int) -> str:
    body = " ".join(f"passage{i}" for i in range(word_count))
    return f"<html><body><main><h1>Manual</h1><p>{body}</p></main></body></html>"


class _ShippedSizeWiki:
    """A throwaway installed wiki of roughly the shipped corpus size.

    The shipped wiki is 2 116 passages. At 140 words per chunk with a 30-word
    overlap that is about 232 000 words; the default here produces a corpus in
    the same order of magnitude, which is what makes the startup budget expire
    the way it does on a real machine.
    """

    def __init__(self, tmp: str, word_count: int = 240000):
        self.root = Path(tmp)
        self.rel = "manual/big-page.html"
        p = self.root / self.rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_corpus_html(word_count), encoding="utf-8")
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        (self.root / "pages-manifest.json").write_text(
            json.dumps({"manifest_version": 1, "pages": {self.rel: digest}}),
            encoding="utf-8")
        (self.root / "pages-manifest.json.asc").write_text("sig", encoding="utf-8")

    def citations(self) -> WikiCitations:
        return WikiCitations(doc_root=str(self.root), gpg_verify=_good_verify)

    def retrieval(self, embedder) -> WikiRetrieval:
        return WikiRetrieval(self.citations(), embedder=embedder)


# ---------------------------------------------------------------- the daemon

class _StubResult:
    """The fields dbus_daemon.InterGenDaemon.ask reads off a route() result."""

    def __init__(self, text: str = "an answer"):
        self.text = text
        self.full_output = ""
        self.source = "test"
        self.handled = True
        self.tool_calls: list = []
        self.tool_results: list = []
        self.answer_linkage = None
        self.used_llm = False
        self.escalated = False
        self.escalation_offer = None
        self.trace_id = ""


class _StubRouter:
    def __init__(self, retrieval):
        self._wiki_retrieval = retrieval
        self.turns = 0

    def route(self, message, **_kw):
        self.turns += 1
        return _StubResult()


def _daemon(retrieval, llama=None):
    """A bare daemon carrying only what ask() and the idle path read.

    object.__new__, not the constructor: building a real daemon starts hardware
    detection, a model server and a web server, none of which this path
    touches.
    """
    d = object.__new__(dd.InterGenDaemon)
    d._paused = False
    d._requests_handled = 0
    d._last_error = None
    d._metrics = None
    d._conversation = None
    d._review_callback_override = lambda *a, **kw: "allow"
    d._router = _StubRouter(retrieval)
    d._llama = None
    d._embed_llama = llama
    d._matcher = None
    return d


class _FakeEmbedLlama:
    """Stands in for the embedding LlamaManager, for the slot question only."""

    def __init__(self, free: bool = True):
        self._free = free

    @property
    def embedding_slot_free(self) -> bool:
        return self._free


class _IdlePathCase(unittest.TestCase):
    """Drives real turns through dbus_daemon.InterGenDaemon.ask."""

    def setUp(self):
        # default_glass_path() reads XDG_STATE_HOME AT CALL TIME, so the
        # redirect has to still be in force when the turn path looks — not only
        # when this fixture was built.
        self._tmp = TemporaryDirectory()
        self._env = mock.patch.dict(
            os.environ, {"XDG_STATE_HOME": self._tmp.name})
        self._env.start()
        from intergen import glass
        self._glass_prev = glass._glass
        glass._glass = None          # swap the singleton; never reload the module
        self._glass_mod = glass

    def tearDown(self):
        self._glass_mod._glass = self._glass_prev
        self._env.stop()
        self._tmp.cleanup()

    @staticmethod
    def _drive(daemon, n: int) -> None:
        for i in range(n):
            daemon.ask(f"turn {i}")

    @staticmethod
    def _settle(retrieval, deadline_s: float = 20.0) -> None:
        """Wait for whatever the turn path started to finish, bounded.

        The pass runs off the turn on purpose (property 2), so a test that read
        the flag the instant ask() returned would be racing the very design it
        is asserting. Bounded, and the assertion still fails if the flag never
        arrives.
        """
        end = time.monotonic() + deadline_s
        while time.monotonic() < end:
            if retrieval.embeddings_ready:
                return
            time.sleep(0.05)


class TheIndexFinishesWhileTheDaemonServesTurns(_IdlePathCase):
    """Property 1 — WIRED."""

    def test_a_degraded_index_completes_across_turns(self):
        with TemporaryDirectory() as tmp:
            wiki = _ShippedSizeWiki(tmp)
            emb = _SingleSlotEmbedder(available=False)   # server not up yet
            retrieval = wiki.retrieval(emb)
            self.assertGreater(
                retrieval.chunk_count, 1500,
                "control: the fixture wiki is not of shipped size, so the "
                "startup budget would not expire the way it does on a real "
                "machine")
            self.assertFalse(
                retrieval.embeddings_ready,
                "control: with the embedding server down the index cannot have "
                "been embedded at construction")

            daemon = _daemon(retrieval, _FakeEmbedLlama(free=True))
            emb.available = True                        # the server comes up

            # FAIL FAST ON THE DEFECT ITSELF. A tree with no caller makes no
            # request at all, and waiting out a completion budget to discover
            # that would make every red run cost minutes for an answer
            # available in seconds.
            #
            # The baseline is taken HERE, after construction, not at zero:
            # building the index already made one (failed) request against the
            # server that was still down, so a bare `> 0` would be satisfied by
            # the startup pass and would pass on a tree with no caller at all.
            # What this asserts is requests the TURN PATH caused.
            calls_at_startup = emb.call_count
            for _ in range(10):
                daemon.ask("what does the manual say")
            self._settle(retrieval, deadline_s=5.0)
            self.assertGreater(
                emb.call_count, calls_at_startup,
                "ten turns went through the daemon's own turn path and NOT ONE "
                "further embedding request was made for the wiki index "
                f"(still {emb.call_count}, the same count construction left). "
                "WikiRetrieval.resume_embedding() exists and works, but no "
                "shipped code path calls it, so an index that started degraded "
                "stays keyword-only until the machine is rebooted.")

            # Then the whole property: bounded per-turn passes finish the
            # corpus. The test never calls resume_embedding itself — that is
            # the point. Bounded by wall clock, not by turn count, so a slow
            # box does not turn a real failure into a timeout.
            deadline = time.monotonic() + 180.0
            while not retrieval.embeddings_ready and time.monotonic() < deadline:
                daemon.ask("what does the manual say")
                self._settle(retrieval, deadline_s=0.3)

            self.assertTrue(
                retrieval.embeddings_ready,
                f"after {daemon._router.turns} turns through the daemon's own "
                f"turn path the wiki index is STILL not embedded "
                f"({emb.call_count} embedding request(s) were made) for "
                f"{retrieval.chunk_count} passages.")

    def test_the_turn_path_does_not_call_resume_when_there_is_nothing_to_do(self):
        """Property 4 — QUIET. A healthy machine pays nothing per turn."""
        with TemporaryDirectory() as tmp:
            wiki = _ShippedSizeWiki(tmp, word_count=400)   # embeds at startup
            emb = _SingleSlotEmbedder()
            retrieval = wiki.retrieval(emb)
            self.assertTrue(
                retrieval.embeddings_ready,
                "control: this corpus is small enough to embed at construction")

            daemon = _daemon(retrieval, _FakeEmbedLlama(free=True))
            calls_before = emb.call_count
            self._drive(daemon, 25)
            self._settle(retrieval, deadline_s=2.0)
            self.assertEqual(
                emb.call_count, calls_before,
                "serving turns against a fully embedded index sent more "
                "requests to the embedding server; a per-turn recovery that "
                "re-embeds when there is nothing to do would compete with live "
                "turns for the one slot it is supposed to protect.")


class TheWorkRunsOffTheTurn(_IdlePathCase):
    """Property 2 — BOUNDED. A turn does not pay for the index."""

    def test_a_turn_does_not_wait_for_the_embedding_pass(self):
        with TemporaryDirectory() as tmp:
            wiki = _ShippedSizeWiki(tmp)
            # 25 ms per input against a 32-input batch is 0.8 s of server work
            # per pass — impossible to miss if ask() waits for it.
            emb = _SingleSlotEmbedder(available=False, cost_per_input=0.025)
            retrieval = wiki.retrieval(emb)
            daemon = _daemon(retrieval, _FakeEmbedLlama(free=True))
            emb.available = True

            began = time.monotonic()
            daemon.ask("what does the manual say")
            elapsed = time.monotonic() - began

            self.assertLess(
                elapsed, 0.5,
                f"ask() took {elapsed:.2f}s while the wiki index caught up. The "
                "recovery pass must run off the turn: a user's reply may not "
                "wait on index maintenance.")


class TheSlotIsCheckedBeforeTheSlotIsUsed(_IdlePathCase):
    """Property 3 — SLOT-AWARE."""

    def test_no_pass_is_started_while_the_embedding_slot_is_busy(self):
        with TemporaryDirectory() as tmp:
            wiki = _ShippedSizeWiki(tmp)
            emb = _SingleSlotEmbedder(available=False)
            retrieval = wiki.retrieval(emb)
            daemon = _daemon(retrieval, _FakeEmbedLlama(free=False))  # busy
            emb.available = True

            calls_before = emb.call_count
            self._drive(daemon, 20)
            self._settle(retrieval, deadline_s=2.0)

            self.assertEqual(
                emb.call_count, calls_before,
                "the turn path started an embedding pass while a request was "
                "already in flight. The embedding server ships --parallel 1: a "
                "second consumer does not get served faster, it waits out its "
                "own timeout while the first request finishes, which is the "
                "starvation this recovery path exists to avoid causing.")

    def test_the_manager_reports_its_slot_and_the_report_moves(self):
        """The slot answer must be MEASURED, not assumed.

        A property that is always True would satisfy every assertion above
        while telling the caller nothing. This drives a real LlamaManager's
        request path and asserts the answer changes while a request is in
        flight.
        """
        from intergen.llama_manager import LlamaManager

        mgr = object.__new__(LlamaManager)
        init = getattr(mgr, "_init_embed_slot", None)
        self.assertIsNotNone(
            init, "LlamaManager offers no embedding-slot state to initialise")
        init()

        self.assertTrue(
            mgr.embedding_slot_free,
            "control: a manager with no request in flight must report its slot "
            "free, or the check below proves nothing")

        seen: list[bool] = []
        entered = threading.Event()
        release = threading.Event()

        def _slow_request(texts, timeout):
            entered.set()
            release.wait(5.0)
            return [[0.0] * 4 for _ in texts]

        with mock.patch.object(mgr, "_embed_one_request", _slow_request):
            t = threading.Thread(
                target=lambda: mgr._embed_one_request_tracked(["x"], 1.0),
                daemon=True)
            t.start()
            self.assertTrue(entered.wait(5.0), "the stand-in request never ran")
            seen.append(mgr.embedding_slot_free)
            release.set()
            t.join(5.0)

        self.assertEqual(
            seen, [False],
            "the manager reported its embedding slot FREE while a request was "
            "in flight. A slot check that cannot see an in-flight request is "
            "not a check.")
        self.assertTrue(
            mgr.embedding_slot_free,
            "the slot was not released after the request finished, so every "
            "later pass would be skipped for ever")


if __name__ == "__main__":
    unittest.main()
