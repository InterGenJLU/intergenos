# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Tests for the demand-corpus merge/dedup tool (M8-6).

Fully deterministic (no embedder, no network, no daemon): near-dup detection is
normalized-signature + token-set Jaccard, so every RED/GREEN check is reproducible.
Schema contract: intergen/tests/demand_corpus/README.md.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from intergen.tests.corpus_loader import CorpusError
from intergen.tests.corpus_merge import (
    distribution_report, entry_signature, jaccard, merge_records, normalize_text,
    read_grounding_keys,
)

CORPUS_DIR = Path(__file__).resolve().parent / "demand_corpus"


def _entry(eid: str, text, *, category="web_search", generator="demand",
           ebc="should-dispatch", grounding=None) -> dict:
    """Build a valid entry. `text` may be a string (single turn) or a list (multi-turn)."""
    if isinstance(text, str):
        turns = [{"user": text}]
    else:
        turns = [{"user": t} for t in text]
    return {
        "id": eid,
        "category": category,
        "intent": "flex an ask",
        "turns": turns,
        "expected_behavior_class": ebc,
        "provenance": {
            "generator": generator,
            "lens": "demand-distribution",
            "grounding": grounding or ["openai-howpeopleuse-2025"],
            "method": "internet-grounded-authored",
        },
    }


def _write(records: list[dict]) -> Path:
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8")
    for r in records:
        tmp.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.close()
    return Path(tmp.name)


class NormalizationTest(unittest.TestCase):
    def test_filler_and_case_stripped(self):
        self.assertEqual(normalize_text("What is my HOSTNAME?"), ["hostname"])

    def test_punctuation_stripped(self):
        self.assertEqual(normalize_text("hostname???"), ["hostname"])

    def test_signature_joins_multiturn(self):
        obj = _entry("x", ["install htop", "yes do it"])
        canonical, tokens = entry_signature(obj)
        self.assertIn("install", tokens)
        self.assertIn("htop", tokens)

    def test_jaccard_bounds(self):
        self.assertEqual(jaccard(frozenset(), frozenset()), 1.0)
        self.assertEqual(jaccard(frozenset({"a"}), frozenset()), 0.0)
        self.assertEqual(jaccard(frozenset({"a", "b"}), frozenset({"a", "b"})), 1.0)


class DedupTest(unittest.TestCase):
    def test_distinct_entries_both_kept(self):
        f = _write([_entry("dd-1", "what's the weather today"),
                    _entry("dd-2", "install the htop package")])
        kept, log = merge_records([f])
        self.assertEqual(len(kept), 2)
        self.assertEqual(log, [])

    def test_exact_duplicate_dropped_first_kept(self):
        f = _write([_entry("dd-1", "install htop"),
                    _entry("dd-2", "install htop")])
        kept, log = merge_records([f])
        self.assertEqual([k["id"] for k in kept], ["dd-1"])
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["dropped_id"], "dd-2")
        self.assertEqual(log[0]["kept_id"], "dd-1")

    def test_near_duplicate_case_punct_filler_collapses(self):
        # RED proof: differs only in case, punctuation, and filler words. If
        # normalization/filler-stripping were removed, these would NOT collapse.
        f = _write([_entry("dd-1", "install htop"),
                    _entry("dd-2", "Please could you install HTOP??")])
        kept, log = merge_records([f])
        self.assertEqual([k["id"] for k in kept], ["dd-1"])
        self.assertEqual(len(log), 1)

    def test_near_duplicate_reordered_tokens_collapses(self):
        # Same token set, reordered — Jaccard == 1.0 -> near-dup.
        f = _write([_entry("dd-1", "check wifi status"),
                    _entry("dd-2", "wifi status check")])
        kept, _log = merge_records([f])
        self.assertEqual(len(kept), 1)

    def test_below_threshold_both_kept(self):
        # Two asks sharing one token but otherwise distinct stay distinct at 0.9.
        f = _write([_entry("dd-1", "install htop please"),
                    _entry("dd-2", "remove firefox package now")])
        kept, _log = merge_records([f])
        self.assertEqual(len(kept), 2)

    def test_threshold_is_tunable(self):
        f = _write([_entry("dd-1", "show my disk space usage"),
                    _entry("dd-2", "show my memory usage")])
        # Distinct at the default 0.9.
        kept_strict, _ = merge_records([f], jaccard_threshold=0.9)
        self.assertEqual(len(kept_strict), 2)
        # Collapsed at a permissive 0.3 (they share show/usage).
        kept_loose, _ = merge_records([f], jaccard_threshold=0.3)
        self.assertEqual(len(kept_loose), 1)

    def test_multiturn_distinct_from_single_turn(self):
        f = _write([_entry("dd-1", "install htop"),
                    _entry("dd-2", ["install htop", "yes confirm"])])
        # The multi-turn flow adds a second turn's tokens -> not an exact dup, and
        # with the extra tokens Jaccard drops below 0.9.
        kept, _log = merge_records([f])
        self.assertEqual(len(kept), 2)


class IdCollisionTest(unittest.TestCase):
    def test_duplicate_id_across_files_hard_errors(self):
        a = _write([_entry("dd-1", "install htop")])
        b = _write([_entry("dd-1", "totally different ask about printers")])
        with self.assertRaises(CorpusError) as cm:
            merge_records([a, b])
        self.assertIn("duplicate id", str(cm.exception))


class GroundingCheckTest(unittest.TestCase):
    def test_registry_keys_parse(self):
        keys = read_grounding_keys(CORPUS_DIR / "grounding_sources.md")
        self.assertIn("openai-howpeopleuse-2025", keys)

    def test_unregistered_grounding_rejected(self):
        f = _write([_entry("dd-1", "hi", grounding=["not-a-real-key"])])
        with self.assertRaises(CorpusError):
            merge_records([f], known_grounding_keys={"openai-howpeopleuse-2025"})


class ReportTest(unittest.TestCase):
    def test_distribution_counts(self):
        f = _write([
            _entry("dd-1", "weather today", category="web_search", generator="demand"),
            _entry("dd-2", ["write a script", "run it"], category="do_for_me",
                   generator="demand", ebc="should-gate"),
            _entry("sf-1", "list all tools", category="capability_question",
                   generator="surface", ebc="route-shape"),
        ])
        kept, log = merge_records([f])
        rep = distribution_report(kept, log)
        self.assertEqual(rep["total"], 3)
        self.assertEqual(rep["single_turn"], 2)
        self.assertEqual(rep["multi_turn"], 1)
        self.assertEqual(rep["by_generator"]["demand"], 2)
        self.assertEqual(rep["by_generator"]["surface"], 1)
        self.assertEqual(rep["by_category"]["web_search"], 1)
        self.assertEqual(rep["by_expected_behavior_class"]["should-gate"], 1)


class DeterminismTest(unittest.TestCase):
    def test_same_inputs_same_output(self):
        recs = [_entry("dd-1", "install htop"),
                _entry("dd-2", "Install HTOP!"),
                _entry("dd-3", "check my wifi")]
        f1 = _write(recs)
        f2 = _write(recs)
        kept1, log1 = merge_records([f1])
        kept2, log2 = merge_records([f2])
        self.assertEqual([k["id"] for k in kept1], [k["id"] for k in kept2])
        # Compare the dedup DECISIONS (dropped -> kept); the 'source' locator
        # carries the input filename, which legitimately differs between two
        # distinct temp files and is not part of the determinism claim.
        decisions = lambda log: [(d["dropped_id"], d["kept_id"]) for d in log]
        self.assertEqual(decisions(log1), decisions(log2))


if __name__ == "__main__":
    unittest.main()
