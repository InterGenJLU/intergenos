# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The how-to index embeds its corpus in BOUNDED requests, and always completes.

WHY THIS EXISTS. Two indexes are built over the same embedding server when the router
starts: the wiki passages and these how-to triggers. The wiki sent its whole corpus in
one request; against a server started with ``--parallel 1`` and reached through a client
with a 30 s timeout that request could not finish in time, and the cost did not end at
the failed request — the client gave up while the server kept working, so the single
slot was still occupied when the next embedding was asked for. Measured on an installed
system 2026-08-25: one gate run spent 609 s, of which 300 s was the abandoned wiki
request and another 300 s was a ONE-TEXT query embedding queued behind it. The wiki side
was bounded into 32-passage requests; the how-to index still sends every trigger it has
in a single request, which is the same shape on the same server through the same client.

WHAT THIS FILE REQUIRES, and what it deliberately does not.

  * No single request may carry more than the batch bound. That is the property the
    30 s client timeout actually depends on, and it is the one asserted here.
  * The index must nonetheless COMPLETE. This is where the how-to index parts company
    with the wiki: teaching is an advertised feature that intergen/router.py says must
    always be on — it logs at CRITICAL when the corpus fails to load — so bounding the
    request size must never become a reason to serve a half-embedded corpus. There is
    no time budget here, on purpose, and the completeness assertion below is what says
    so in enforceable form.
  * Retrieval must not change. Splitting the requests is a transport change; the matrix
    the index scores against must be the same one it scored against before.
  * A batch that fails leaves NO half index. A query scored against whichever triggers
    happened to be embedded first would rank by accident of ordering.
"""

from __future__ import annotations

import importlib.util
import json
import re
import tempfile
import unittest
import zlib
from pathlib import Path

from intergen import howto
from intergen.howto import HowtoCorpus

# THE REQUIREMENT, stated here rather than read from the module under test. A test that
# takes its bound from the implementation cannot fail when the implementation has no
# bound at all — it errors on the missing name instead, which is a different finding
# and a weaker one. 64 is the ceiling this file enforces: it is twice the 32 the wiki
# index settled on for passages an order of magnitude longer, so a how-to corpus that
# needs more than 64 texts in one request has grown past what the 30 s client timeout
# was ever measured against.
_REQUIRED_MAX_TEXTS_PER_REQUEST = 64

# A synthetic corpus size that exceeds the requirement several times over, fixed here
# so the test does not depend on any constant the implementation may or may not have.
_MANY_TRIGGERS = 200

_STOP = {"how", "do", "i", "to", "the", "a", "my", "is", "what", "command", "s"}


def _bow(text: str, dim: int = 256) -> list[float]:
    vec = [0.0] * dim
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if w in _STOP:
            continue
        vec[zlib.crc32(w.encode()) % dim] += 1.0
    return vec


class _Recorder:
    """Deterministic embedder that records the SIZE of every request it is given."""

    def __init__(self, fail_from: int | None = None):
        self.requests: list[int] = []
        self._fail_from = fail_from

    def __call__(self, texts):
        texts = list(texts)
        self.requests.append(len(texts))
        if self._fail_from is not None and len(self.requests) > self._fail_from:
            return []
        return [_bow(t) for t in texts]


def _write_corpus(directory: Path, trigger_count: int) -> None:
    """A corpus with a known number of triggers, so the bound can be exercised."""
    entries = [{
        "id": f"gen-{i}",
        "domain": "test",
        "triggers": [f"trigger phrase number {i}"],
        "answer": f"answer {i}",
    } for i in range(trigger_count)]
    # A domain file is a JSON LIST of entry dicts — checked against the shipped
    # intergen/data/howto/pkm.json, because a fixture the loader silently skips
    # would leave every case below measuring an empty corpus and passing.
    (directory / "generated.json").write_text(
        json.dumps(entries), encoding="utf-8")


class BatchBoundTests(unittest.TestCase):

    def test_no_single_request_carries_the_whole_shipped_corpus(self):
        """The real shipped corpus, at its real size, through a recording embedder."""
        rec = _Recorder()
        corpus = HowtoCorpus(embedder=rec)
        self.assertGreater(corpus.entry_count, 0, "the shipped corpus is empty")
        self.assertTrue(rec.requests, "no embedding request was issued at all")
        oversized = [n for n in rec.requests if n > _REQUIRED_MAX_TEXTS_PER_REQUEST]
        self.assertEqual(
            oversized, [],
            f"embedding request(s) of {oversized} text(s) exceed the "
            f"{_REQUIRED_MAX_TEXTS_PER_REQUEST}-text bound this file requires; "
            f"request sizes were {rec.requests}")

    def test_a_corpus_larger_than_one_batch_takes_several_requests(self):
        """A bound nothing ever reaches would prove nothing."""
        with tempfile.TemporaryDirectory() as td:
            _write_corpus(Path(td), _MANY_TRIGGERS)
            rec = _Recorder()
            HowtoCorpus(embedder=rec, data_dir=td)
            self.assertGreater(
                len(rec.requests), 1,
                f"a corpus of {_MANY_TRIGGERS} triggers was embedded in "
                f"{len(rec.requests)} request(s)")
            self.assertTrue(
                all(n <= _REQUIRED_MAX_TEXTS_PER_REQUEST for n in rec.requests),
                f"request sizes {rec.requests} exceed the bound")

    def test_the_index_completes_for_the_whole_corpus(self):
        """Teaching is always-on: bounding the requests must not bound the WORK."""
        if importlib.util.find_spec("numpy") is None:
            self.skipTest("numpy not available — embedding path skipped")
        with tempfile.TemporaryDirectory() as td:
            _write_corpus(Path(td), _MANY_TRIGGERS)
            rec = _Recorder()
            corpus = HowtoCorpus(embedder=rec, data_dir=td)
            self.assertIsNotNone(
                corpus._embeddings,
                "the how-to index published no embeddings, so every teaching query "
                "falls back to keyword retrieval")
            self.assertEqual(corpus._embeddings.shape[0], len(corpus._trigger_texts),
                             "the index covers fewer triggers than the corpus holds")
            self.assertEqual(sum(rec.requests), len(corpus._trigger_texts),
                             "the requests do not account for every trigger exactly once")

    def test_retrieval_is_unchanged_by_the_split(self):
        """The matrix scored against must be the one a single request produced."""
        if importlib.util.find_spec("numpy") is None:
            self.skipTest("numpy not available — embedding path skipped")

        def one_shot(texts):
            return [_bow(t) for t in texts]

        batched = HowtoCorpus(embedder=_Recorder())
        control = HowtoCorpus(embedder=one_shot)
        for query in ("how do I update my system",
                      "how do I install a package",
                      "how do I remove a package"):
            got, got_score = batched.retrieve(query)
            want, want_score = control.retrieve(query)
            self.assertEqual(getattr(got, "id", None), getattr(want, "id", None),
                             f"batching changed which entry {query!r} retrieves")
            self.assertAlmostEqual(got_score, want_score, places=6,
                                   msg=f"batching changed the score for {query!r}")

    def test_a_failed_batch_leaves_no_half_index(self):
        """A partially embedded corpus must never be scored against."""
        if importlib.util.find_spec("numpy") is None:
            self.skipTest("numpy not available — embedding path skipped")
        with tempfile.TemporaryDirectory() as td:
            _write_corpus(Path(td), _MANY_TRIGGERS)
            rec = _Recorder(fail_from=1)
            corpus = HowtoCorpus(embedder=rec, data_dir=td)
            self.assertIsNone(
                corpus._embeddings,
                "an index was published from a corpus whose embedding failed part "
                "way through; queries would rank by which triggers embedded first")


if __name__ == "__main__":
    unittest.main()
