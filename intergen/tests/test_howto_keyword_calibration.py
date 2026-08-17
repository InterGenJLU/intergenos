# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The how-to corpus's KEYWORD fallback — the path a machine serves on when its
embedding server is unavailable.

Every number asserted here was MEASURED against the shipped corpus by
``scripts/howto-keyword-calibration.py``; re-run that harness to re-derive any
of them. These cases pin the behaviour the measurement chose, so a later edit
to the scorer or the floors has to face the same evidence.

The tests deliberately use the REAL shipped corpus rather than a fixture: the
floors are properties of that corpus, and a fixture would let the corpus drift
underneath them without anything failing.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from intergen import howto
from intergen.howto import HowtoCorpus


def _corpus() -> HowtoCorpus:
    """The shipped corpus with NO embedder — exactly the state the daemon's
    corpus is in when the embedding server never came up."""
    return HowtoCorpus(embedder=None)


class KeywordFloorTests(unittest.TestCase):
    """The two floors, and the two failure shapes each one exists to reject."""

    def setUp(self):
        self.corpus = _corpus()

    def test_off_corpus_question_containing_a_real_trigger_is_refused(self):
        # The binding negative of the calibration: this question CONTAINS the
        # real trigger "how do I add a user", so its word-overlap score is 0.5 —
        # inside the positive range, and no overlap floor alone can reject it.
        # The vocabulary condition does: gym and membership are words the corpus
        # has never seen, so only half the question is anything it knows about.
        entry, score = self.corpus.retrieve("how do I add a user to my gym membership")
        self.assertIsNone(entry,
                          f"an off-corpus question must not be answered from the "
                          f"curated corpus (scored {score})")

    def test_the_vocabulary_condition_is_what_rejects_it(self):
        # Guards the guard: if the rejection above ever came from the overlap
        # floor instead, this case would fail and say so. The raw score is read
        # with the gate opened (threshold=0.0) and must still be at the measured
        # 0.5 — i.e. high enough that the overlap floor alone would serve it.
        _, score = self.corpus.retrieve("how do I add a user to my gym membership",
                                        threshold=0.0)
        self.assertGreaterEqual(score, howto.KEYWORD_THRESHOLD)
        self.assertAlmostEqual(score, 0.5, places=4)

    def test_ordinary_off_corpus_questions_are_refused(self):
        for query in ("write me a short poem about a lighthouse",
                      "what is the capital of France",
                      "how do I train for a marathon",
                      "how do I change a flat tire",
                      "how do I file my taxes this year"):
            with self.subTest(query=query):
                entry, score = self.corpus.retrieve(query)
                self.assertIsNone(entry, f"{query!r} scored {score}")

    def test_questions_the_corpus_covers_are_served(self):
        """Held-out-trigger positives, the same construction the harness uses.

        The question is asked of a corpus its own phrasing has been REMOVED
        from, so nothing is being tested against itself — the entry has to be
        reached through its other phrasings, which is what a real user query
        does. Asking the intact corpus would prove nothing: the query would BE
        an indexed trigger and score 1.0 under any normalization.

        All three were measured refused by the previous union-normalized floor
        (0.3333, 0.3333, 0.2857 against a floor of 0.5) and served now.
        """
        import copy
        raw = []
        data_dir = Path(howto.__file__).resolve().parent / "data" / "howto"
        for path in sorted(data_dir.glob("*.json")):
            raw.extend(json.loads(path.read_text(encoding="utf-8")))
        for query in ("how do I delete a file",
                      "how do I check the firewall",
                      "which printers are installed"):
            with self.subTest(query=query):
                held = []
                for item in copy.deepcopy(raw):
                    if query in item.get("triggers", ()):
                        item["triggers"] = [t for t in item["triggers"]
                                            if t != query]
                        if not item["triggers"]:
                            continue
                    held.append(item)
                self.assertLess(len(json.dumps(held)), len(json.dumps(raw)),
                                "the held-out trigger must actually be gone")
                with tempfile.TemporaryDirectory() as td:
                    (Path(td) / "held.json").write_text(json.dumps(held),
                                                        encoding="utf-8")
                    corpus = HowtoCorpus(embedder=None, data_dir=Path(td))
                    entry, score = corpus.retrieve(query)
                self.assertIsNotNone(entry, f"{query!r} scored {score}")

    def test_score_is_the_share_of_the_query_a_trigger_supplies(self):
        # The scorer's contract, pinned on a query whose arithmetic is checkable
        # by hand: four content words (add, user, gym, membership), of which the
        # trigger "how do I add a user" supplies two -> 0.5. Under the previous
        # union-normalized (Jaccard) score the same pair also gives 2/4; the
        # discriminating case is the one below, where the query is a SUBSET of
        # the trigger's words.
        _, score = self.corpus.retrieve("how do I add a user to my gym membership",
                                        threshold=0.0)
        self.assertAlmostEqual(score, 2 / 4, places=4)

    def test_a_terse_query_fully_covered_by_a_trigger_scores_one(self):
        """Query-normalized: every content word of the question is supplied, so
        the score is 1.0 no matter how many further words the trigger carries.

        Each of these is a strict SUBSET of a real trigger's words rather than a
        trigger itself — which is what makes the case discriminating. Measured
        under the previous union-normalized score: 0.5, 0.6667, 0.6667."""
        for query in ("wifi", "dns server", "free memory"):
            with self.subTest(query=query):
                _, score = self.corpus.retrieve(query, threshold=0.0)
                self.assertAlmostEqual(score, 1.0, places=4)

    def test_floors_are_the_measured_values(self):
        # Not a tautology: it fails the moment someone edits a floor without
        # re-running the harness, which is the whole point of a measured floor.
        self.assertAlmostEqual(howto.KEYWORD_THRESHOLD, 0.34, places=4)
        self.assertAlmostEqual(howto.KEYWORD_MIN_KNOWN_SHARE, 0.60, places=4)
        self.assertAlmostEqual(howto.KEYWORD_STRONG_THRESHOLD, 0.68, places=4)


class KeywordFloorScaleTests(unittest.TestCase):
    """A floor is only meaningful on the scale of the path that produced the
    score. These pin that the corpus picks the floor per path."""

    @staticmethod
    def _bow(texts):
        """A deterministic stand-in for the embedding server, so the embedding
        index builds without one. Bag-of-words over hashed buckets, like the
        existing corpus tests."""
        import re
        import zlib
        out = []
        for text in texts:
            vec = [0.0] * 128
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                vec[zlib.crc32(word.encode()) % 128] += 1.0
            out.append(vec)
        return out

    def _needs_numpy(self):
        import importlib.util
        if importlib.util.find_spec("numpy") is None:
            self.skipTest("numpy not available — the embedding path needs it")

    def test_embedder_lost_after_index_build_falls_back_to_the_keyword_floor(self):
        """The degraded-mid-session shape: the index built while the embedding
        server was up, then the server went away. The score now comes from the
        keyword fallback, and gating it with the COSINE floor (0.72) refused
        nearly everything the corpus covers."""
        self._needs_numpy()
        calls = {"n": 0}

        def embedder(texts):
            # Answers while the index is built, then stops — a server that dies
            # after startup, which is the observed field shape.
            calls["n"] += 1
            return self._bow(texts) if calls["n"] == 1 else None

        corpus = HowtoCorpus(embedder=embedder)
        self.assertIsNotNone(corpus._embeddings,  # noqa: SLF001
                             "the embedding index must have been built first")
        entry, score = corpus.retrieve("verify package integrity")
        self.assertGreater(calls["n"], 1, "the query must have asked the embedder")
        self.assertLess(score, howto.DEFAULT_THRESHOLD,
                        "this query's keyword score is below the COSINE floor — "
                        "which is exactly why applying that floor here was wrong")
        self.assertIsNotNone(
            entry,
            "a keyword-path score must be gated by the keyword floor, not by "
            "the embedding floor")

    def test_strong_is_asked_for_by_name_not_by_a_number(self):
        # The router used to pass a cosine-scale number (0.82), which became
        # meaningless the moment the corpus was on the keyword path.
        corpus = _corpus()
        served, _ = corpus.retrieve("take a screenshot", strong=True)
        self.assertIsNotNone(served,
                             "a question a trigger covers word-for-word is a "
                             "strong match on any scale")
        refused, score = corpus.retrieve("create a scripts folder", strong=True)
        self.assertIsNone(refused,
                          f"a plain imperative is an action for the tool path, "
                          f"not a lesson (scored {score})")
        # ... and the same query DOES reach the corpus when the caller does not
        # require a strong match, so the strong floor is what made the
        # difference and not the vocabulary condition or the normal floor.
        normal, _ = corpus.retrieve("create a scripts folder")
        self.assertIsNotNone(normal)


class KeywordVocabularyTests(unittest.TestCase):
    """The vocabulary condition, on a corpus small enough to reason about."""

    def _write(self, d: Path, entries: list[dict]) -> HowtoCorpus:
        (d / "x.json").write_text(json.dumps(entries), encoding="utf-8")
        return HowtoCorpus(embedder=None, data_dir=d)

    def test_a_question_whose_subject_is_unknown_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            corpus = self._write(Path(td), [{
                "id": "restart-service", "domain": "x",
                "triggers": ["how do I restart a service"],
                "answer": "systemctl restart <name>", "doc_source": "",
            }])
            # Every content word of the trigger is present, so the overlap
            # score is high — but two thirds of the question is about something
            # the corpus has never heard of.
            entry, score = corpus.retrieve(
                "how do I restart a service at the laundromat downtown")
            self.assertGreaterEqual(score, howto.KEYWORD_THRESHOLD)
            self.assertIsNone(entry)
            # The same question without the unknown subject is answered.
            entry2, _ = corpus.retrieve("how do I restart a service")
            self.assertIsNotNone(entry2)

    def test_the_condition_is_a_share_not_a_ban_on_unknown_words(self):
        # A real question can carry a word the corpus never uses; only a
        # question that is MOSTLY unknown words is out of domain.
        with tempfile.TemporaryDirectory() as td:
            corpus = self._write(Path(td), [{
                "id": "restart-service", "domain": "x",
                "triggers": ["how do I restart a service"],
                "answer": "systemctl restart <name>", "doc_source": "",
            }])
            entry, _ = corpus.retrieve("how do I restart a stuck service")
            self.assertIsNotNone(entry, "one unknown word in three must not "
                                        "make a question out of domain")


if __name__ == "__main__":
    unittest.main()
