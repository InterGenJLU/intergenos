# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Tests for the teaching how-to corpus (intergen.howto).

The embedding path is exercised with a deterministic bag-of-words fake embedder
(crc32-hashed word buckets) so cosine retrieval is reproducible without the real
nomic-embed server; the keyword fallback and ground-truth filter need no embedder.
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
import zlib
from pathlib import Path

from intergen.howto import HowtoCorpus

_STOP = {"how", "do", "i", "to", "the", "a", "my", "is", "what", "command", "s"}


def _bow_embed(texts):
    """Deterministic bag-of-words embedding — similar texts share word buckets, so
    cosine behaves like a real (if crude) semantic match. Plain lists, no numpy."""
    dim = 256
    out = []
    for t in texts:
        vec = [0.0] * dim
        for w in re.findall(r"[a-z0-9]+", t.lower()):
            if w in _STOP:
                continue
            vec[zlib.crc32(w.encode()) % dim] += 1.0
        out.append(vec)
    return out


class _FakeReference:
    def __init__(self, installed: set[str]):
        self._installed = installed

    def is_installed(self, tool: str) -> bool:
        return tool in self._installed


class ShippedPkmCorpusTests(unittest.TestCase):
    """Exercises the REAL shipped intergen/data/howto/pkm.json."""

    def test_loads_the_pkm_corpus(self):
        corpus = HowtoCorpus(embedder=None)  # default data dir = shipped corpus
        self.assertGreaterEqual(corpus.entry_count, 6)

    def test_embedding_retrieval_picks_the_update_howto(self):
        import importlib.util
        if importlib.util.find_spec("numpy") is None:
            self.skipTest("numpy not available — embedding path skipped")
        corpus = HowtoCorpus(embedder=_bow_embed)
        entry, score = corpus.retrieve("how do I update my system")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.id, "pkm-update-system")
        self.assertGreater(score, 0.72)
        # explain-FIRST then offer: the update how-to carries a gated action.
        self.assertIsNotNone(entry.action)
        self.assertIn("pkm", entry.action.command)
        self.assertEqual(entry.action.tool, "manage_packages")

    def test_embedding_retrieval_distinguishes_install_from_remove(self):
        import importlib.util
        if importlib.util.find_spec("numpy") is None:
            self.skipTest("numpy not available — embedding path skipped")
        # Near-trigger queries: the bag-of-words test embedder proves correct
        # INDEXING (right query → right entry); semantic generalization
        # ("program" ≈ "software") is the real nomic-embed's job, validated in the
        # end-to-end dyno, not with this crude fake.
        corpus = HowtoCorpus(embedder=_bow_embed)
        ins, _ = corpus.retrieve("how do I install an application")
        rem, _ = corpus.retrieve("how do I remove a package")
        self.assertEqual(ins.id, "pkm-install")
        self.assertEqual(rem.id, "pkm-remove")

    def test_unrelated_query_returns_nothing(self):
        corpus = HowtoCorpus(embedder=_bow_embed)
        entry, _ = corpus.retrieve("what is the meaning of life")
        self.assertIsNone(entry)

    def test_keyword_fallback_without_embedder(self):
        # No embedder → the deterministic keyword fallback still answers.
        corpus = HowtoCorpus(embedder=None)
        entry, score = corpus.retrieve("verify package integrity")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.id, "pkm-verify")
        self.assertGreaterEqual(score, 0.5)

    def test_action_present_only_where_curated(self):
        corpus = HowtoCorpus(embedder=None)
        by_id = {e.id: e for e in corpus._entries}
        self.assertIsNotNone(by_id["pkm-update-system"].action)
        self.assertIsNone(by_id["pkm-search"].action)  # parameterized → no auto-offer


class ShippedCorpusIntegrityTests(unittest.TestCase):
    """Guards the SHIPPED corpus itself so a typo/drift in any domain file is caught
    here rather than silently mis-routing at runtime."""

    _V1_DOMAINS = {"pkm", "services", "files", "users", "networking", "intergenos"}

    @classmethod
    def setUpClass(cls):
        # The valid-tool ground truth is the REAL registry's own discovery, not
        # a hand-maintained copy of it — a hand list drifts silently the moment
        # a tool is added (it was missing take_screenshot when this was fixed).
        # Discovery is filesystem-only (intergen/tools/*.py), no daemon needed.
        from intergen.tool_registry import ToolRegistry
        registry = ToolRegistry()
        registry.discover_tools()
        cls._valid_tools = set(registry.get_all_names())

    def setUp(self):
        self.corpus = HowtoCorpus(embedder=None)

    def test_registry_discovery_is_sane(self):
        # Guards the guard: if discovery ever returns a near-empty set, the
        # real-tool assertion below would pass vacuously-wrong or fail
        # confusingly. Anchor on tools the corpus itself depends on.
        self.assertGreaterEqual(len(self._valid_tools), 8,
                                f"registry discovery collapsed: {sorted(self._valid_tools)}")
        self.assertIn("manage_packages", self._valid_tools)
        self.assertIn("take_screenshot", self._valid_tools)

    def test_all_v1_domains_present(self):
        domains = {e.domain for e in self.corpus._entries}
        self.assertTrue(self._V1_DOMAINS.issubset(domains),
                        f"missing domains: {self._V1_DOMAINS - domains}")

    def test_every_action_names_a_real_tool(self):
        for e in self.corpus._entries:
            if e.action is not None:
                self.assertIn(e.action.tool, self._valid_tools,
                              f"{e.id} action tool {e.action.tool!r} is not a real tool")

    def test_every_entry_has_a_doc_source(self):
        # The anti-drift invariant: each entry is bound to the wiki/docs page it
        # must agree with, so the teaching corpus cannot silently diverge.
        for e in self.corpus._entries:
            self.assertTrue(e.doc_source.strip(), f"{e.id} has no doc_source")

    def test_entry_ids_are_unique(self):
        ids = [e.id for e in self.corpus._entries]
        self.assertEqual(len(ids), len(set(ids)), "duplicate how-to id")


class CorpusMechanicsTests(unittest.TestCase):
    """Loading resilience + ground-truth filter, on a synthetic corpus dir."""

    def _write_corpus(self, d: Path, entries: list[dict], name: str = "x.json"):
        (d / name).write_text(json.dumps(entries), encoding="utf-8")

    def test_ground_truth_filter_hides_entry_for_absent_tool(self):
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            self._write_corpus(dp, [{
                "id": "needs-missing", "domain": "x",
                "triggers": ["how do I frobnicate the widget"],
                "answer": "run frobnicate", "doc_source": "",
                "requires": ["frobnicate-xyz-not-real"],
            }])
            ref = _FakeReference(installed=set())  # nothing installed
            corpus = HowtoCorpus(embedder=None, data_dir=dp, reference=ref)
            entry, _ = corpus.retrieve("how do I frobnicate the widget")
            self.assertIsNone(entry, "entry requiring an absent tool must be filtered")
            # With the tool present, it surfaces.
            ref2 = _FakeReference(installed={"frobnicate-xyz-not-real"})
            corpus2 = HowtoCorpus(embedder=None, data_dir=dp, reference=ref2)
            entry2, _ = corpus2.retrieve("how do I frobnicate the widget")
            self.assertIsNotNone(entry2)

    def test_malformed_file_is_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d)
            (dp / "bad.json").write_text("{ not valid json", encoding="utf-8")
            self._write_corpus(dp, [{
                "id": "good", "domain": "x",
                "triggers": ["how do I do the good thing"],
                "answer": "the good answer", "doc_source": "",
            }], name="good.json")
            corpus = HowtoCorpus(embedder=None, data_dir=dp)
            self.assertEqual(corpus.entry_count, 1)
            entry, _ = corpus.retrieve("how do I do the good thing")
            self.assertEqual(entry.id, "good")

    def test_absent_corpus_dir_is_empty_not_fatal(self):
        corpus = HowtoCorpus(embedder=None, data_dir="/nonexistent/howto/dir")
        self.assertEqual(corpus.entry_count, 0)
        self.assertEqual(corpus.retrieve("anything"), (None, 0.0))


if __name__ == "__main__":
    unittest.main()
