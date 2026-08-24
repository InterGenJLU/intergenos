# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Startup embedding of the installed wiki: batch it, bound it, recover from it.

The defect these tests pin down
-------------------------------
WikiRetrieval._embed_chunks submits EVERY passage in one call::

    vectors = self._embedder([c.text for c in self._chunks])

On an installed system that is 2 116 passages in a single request to an
embedding server started with ``--parallel 1``, under a 30 s client timeout.
It times out every time. The client abandons the request; the server keeps
working through the batch, so the single embedding slot stays occupied at
exactly the moment the web server begins accepting turns — which is how this
defect produces the turn-lifecycle failure in
intergen/tests/test_turn_lifecycle_contract.py.

Nothing recovers afterwards. _build_index runs only from __init__, so there is
no rebuild, no refresh and no watchdog, and wiki answering silently falls back
to keyword matching for the entire life of the daemon, on every boot. The
intent layer already has the recovery path this one lacks
(SemanticMatcher.refresh_pending_intents).

Observed on four of four boots in one machine's journal and independently on
seven boots of another:

    indexed 2116 passage(s) from 87 verified page(s)
    embed() request failed: timed out          (+30.0 s)
    embedder returned nothing; keyword fallback only
    Web server started
    embed() request failed: timed out          (a second consumer, starved)

Why the existing tests did not catch it
---------------------------------------
intergen/tests/test_wiki_retrieval.py embeds a handful of chunks with an
instant in-process stub. The defect is a function of corpus size, server
concurrency and timeout, and a stub reproduces none of the three. The embedder
stub here models the constraint that actually exists: a server that serves one
request at a time and fails the request outright when the batch is too large
for the client's patience.

The three properties asserted here:

  1. BATCHED    — no single embedding request carries the whole corpus, so a
                  large wiki cannot produce a request that is guaranteed to
                  time out, and cannot leave an abandoned batch occupying the
                  slot the first user turn needs.
  2. BOUNDED    — construction spends a bounded amount of time embedding.
                  Startup belongs to the daemon, not to the index.
  3. RECOVERABLE— an index that started degraded can be completed later,
                  instead of being keyword-only until the next reboot.
"""

from __future__ import annotations

import hashlib
import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from intergen import wiki_retrieval as wiki_module
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

    ``capacity`` models what the shipped deployment actually does: a request
    larger than the client is prepared to wait for fails outright and returns
    nothing, exactly as llama_manager.embed does when urlopen hits its timeout.
    ``cost_per_input`` models the serial work of a --parallel 1 server.
    ``available`` models a server that is not up yet.
    """

    DIM = 8

    def __init__(self, capacity: "int | None" = None,
                 cost_per_input: float = 0.0, available: bool = True):
        self.capacity = capacity
        self.cost_per_input = cost_per_input
        self.available = available
        self.calls: list[int] = []      # batch size of every request made

    def __call__(self, texts: "list[str]") -> "list[list[float]] | None":
        self.calls.append(len(texts))
        if not self.available:
            return None
        if self.capacity is not None and len(texts) > self.capacity:
            return None                 # the request timed out; nothing comes back
        if self.cost_per_input:
            time.sleep(self.cost_per_input * len(texts))
        return [[float(len(t) % 7)] * self.DIM for t in texts]

    @property
    def largest_request(self) -> int:
        return max(self.calls) if self.calls else 0


def _embeddings_ready(retrieval: WikiRetrieval) -> bool:
    """True when the embedding retrieval path is live for the WHOLE index.

    Read through the public flag when the module offers one, and otherwise off
    the private array that retrieve() itself consults — so this measures the
    same property before and after the correction, and a red run reports a
    behaviour failure rather than a missing attribute.
    """
    flag = getattr(retrieval, "embeddings_ready", None)
    if flag is None:
        return retrieval._embeddings is not None
    return bool(flag)


def _corpus_html(word_count: int) -> str:
    body = " ".join(f"passage{i}" for i in range(word_count))
    return f"<html><body><main><h1>Manual</h1><p>{body}</p></main></body></html>"


class _BigWiki:
    """A throwaway installed wiki large enough to need more than one request."""

    def __init__(self, tmp: str, word_count: int = 12000):
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


class BatchingTests(unittest.TestCase):
    """Property 1 — no request carries the whole corpus."""

    def test_the_corpus_really_is_larger_than_one_batch(self):
        # Control: if the fixture stopped producing a multi-batch corpus these
        # tests would pass without asserting anything.
        with TemporaryDirectory() as tmp:
            wiki = _BigWiki(tmp)
            emb = _SingleSlotEmbedder()
            retrieval = wiki.retrieval(emb)
            self.assertGreater(
                retrieval.chunk_count, 64,
                "the fixture wiki is too small to exercise batching")

    def test_no_single_request_carries_the_whole_corpus(self):
        with TemporaryDirectory() as tmp:
            wiki = _BigWiki(tmp)
            emb = _SingleSlotEmbedder()
            retrieval = wiki.retrieval(emb)
            bound = getattr(wiki_module, "_EMBED_BATCH", None)
            self.assertIsNotNone(
                bound,
                "wiki_retrieval declares no per-request batch bound, so the "
                "whole corpus goes to the embedding server in one call.")
            self.assertLessEqual(
                emb.largest_request, int(bound),
                f"largest embedding request was {emb.largest_request} inputs "
                f"for {retrieval.chunk_count} passages; a request that size "
                "against a one-slot server under a client timeout is the "
                "boot-time failure this gate exists for.")

    def test_the_index_embeds_against_a_single_slot_server(self):
        # The server refuses anything larger than it can serve inside the
        # client's patience — the shipped condition.
        with TemporaryDirectory() as tmp:
            wiki = _BigWiki(tmp)
            emb = _SingleSlotEmbedder(capacity=64)
            retrieval = wiki.retrieval(emb)
            self.assertTrue(
                _embeddings_ready(retrieval),
                "the wiki index fell back to keyword matching against a server "
                "that was up and answering — every request was simply too big "
                "to succeed.")


class StartupBudgetTests(unittest.TestCase):
    """Property 2 — construction does not hold the daemon open indefinitely."""

    def test_construction_is_bounded_when_embedding_is_slow(self):
        budget = getattr(wiki_module, "_STARTUP_EMBED_BUDGET_S", None)
        self.assertIsNotNone(
            budget,
            "wiki_retrieval declares no startup embedding budget, so building "
            "the index blocks daemon startup for as long as the embedding "
            "server takes.")
        with TemporaryDirectory() as tmp:
            wiki = _BigWiki(tmp)
            original = wiki_module._STARTUP_EMBED_BUDGET_S
            wiki_module._STARTUP_EMBED_BUDGET_S = 0.3
            try:
                emb = _SingleSlotEmbedder(cost_per_input=0.002)
                began = time.monotonic()
                wiki.retrieval(emb)
                elapsed = time.monotonic() - began
            finally:
                wiki_module._STARTUP_EMBED_BUDGET_S = original
            self.assertLess(
                elapsed, 1.5,
                f"building the index took {elapsed:.2f}s against a 0.3s budget; "
                "startup time belongs to the daemon, not to the index.")


class RecoveryTests(unittest.TestCase):
    """Property 3 — a degraded start is not permanent."""

    def test_a_degraded_start_can_be_completed_later(self):
        with TemporaryDirectory() as tmp:
            wiki = _BigWiki(tmp)
            emb = _SingleSlotEmbedder(available=False)   # server not up yet
            retrieval = wiki.retrieval(emb)
            self.assertFalse(
                _embeddings_ready(retrieval),
                "control: with the server down the index cannot be embedded")
            resume = getattr(retrieval, "resume_embedding", None)
            self.assertIsNotNone(
                resume,
                "WikiRetrieval offers no way to finish embedding after a "
                "degraded start, so a boot that lost its embedding window "
                "serves keyword-only matching until the machine is rebooted.")
            emb.available = True
            for _ in range(50):                  # bounded; each pass does a slice
                if _embeddings_ready(retrieval):
                    break
                resume()
            self.assertTrue(
                _embeddings_ready(retrieval),
                "the index never completed even with the embedding server back.")

    def test_resuming_a_complete_index_is_a_no_op(self):
        with TemporaryDirectory() as tmp:
            wiki = _BigWiki(tmp)
            emb = _SingleSlotEmbedder()
            retrieval = wiki.retrieval(emb)
            resume = getattr(retrieval, "resume_embedding", None)
            self.assertIsNotNone(resume, "no resume path")
            for _ in range(20):
                if _embeddings_ready(retrieval):
                    break
                resume()
            self.assertTrue(_embeddings_ready(retrieval))
            calls_when_done = len(emb.calls)
            resume()
            resume()
            self.assertEqual(
                len(emb.calls), calls_when_done,
                "resuming a fully embedded index sent more requests to the "
                "embedding server; a recovery path that re-embeds on every "
                "call would compete with live turns for the one slot.")


class SmallCorpusControlTests(unittest.TestCase):
    """A wiki smaller than one batch must still behave exactly as before."""

    def test_a_small_corpus_embeds_in_a_single_pass(self):
        with TemporaryDirectory() as tmp:
            wiki = _BigWiki(tmp, word_count=100)
            emb = _SingleSlotEmbedder()
            retrieval = wiki.retrieval(emb)
            self.assertEqual(retrieval.chunk_count, 1)
            self.assertEqual(len(emb.calls), 1)


if __name__ == "__main__":
    unittest.main()
