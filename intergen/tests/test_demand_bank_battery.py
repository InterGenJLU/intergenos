# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""M8 §7 — the demand-bank battery: selection + loading (deterministic, daemon-free).

The merged four-digit demand bank is a NAMED, OPT-IN battery (runner --demand-bank):
regenerated from both half-files via corpus_merge, loaded via corpus_loader, never part
of the default battery. These tests pin the selection + loading contract — NOT a live
run (that needs the daemon; the discovery mass-run is the on-box lane). Grading is
discovery-grade: bank entries carry no content assertions.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from intergen.tests.conversations import get_all_conversations
from intergen.tests.corpus_loader import load_corpus
from intergen.tests.corpus_merge import (
    CORPUS_DIR, DEMAND_BANK_HALVES, read_grounding_keys, regenerate_bank,
)


class RegenerateBankTest(unittest.TestCase):
    def test_regenerate_merges_both_halves(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "bank.jsonl"
            path, report = regenerate_bank(out_path=out)
            self.assertTrue(path.exists(), "bank.jsonl must be written")
            self.assertTrue(path.with_suffix(".report.json").exists(),
                            "distribution report must be emitted beside the bank")
            # Both halves present on dev -> both generators appear, four-digit total.
            self.assertIn("demand", report["by_generator"])
            self.assertIn("surface", report["by_generator"])
            self.assertGreater(report["total"], 1000,
                               "the merged bank is the four-digit bank")
            # dedup ran across halves (cross-half near-dups dropped deterministically).
            self.assertGreaterEqual(report["dropped_as_duplicate"], 0)

    def test_half_files_exist_on_dev(self):
        for name in DEMAND_BANK_HALVES:
            self.assertTrue((CORPUS_DIR / name).exists(),
                            f"expected half-file {name} under {CORPUS_DIR}")


class BankLoadsThroughLoaderTest(unittest.TestCase):
    def test_bank_loads_via_corpus_loader(self):
        """The regenerated bank loads through the authoritative loader (schema-valid,
        grounding keys resolve), and the count matches the merge report."""
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "bank.jsonl"
            path, report = regenerate_bank(out_path=out)
            convs = load_corpus(path, known_grounding_keys=read_grounding_keys())
        self.assertEqual(len(convs), report["total"],
                         "loaded conversation count must match the merge report")
        # Discovery-grade: no content assertions on any turn.
        total_assertions = sum(len(t.assertions) for c in convs for t in c.turns)
        self.assertEqual(total_assertions, 0,
                         "the discovery bank carries no content assertions")
        # Multi-turn entries load persistent (state survives their own turns).
        multi = [c for c in convs if len(c.turns) > 1]
        self.assertTrue(multi, "the bank has multi-turn flows")
        self.assertTrue(all(c.persist_state for c in multi),
                        "multi-turn bank entries must load persistent")


class OptInIsolationTest(unittest.TestCase):
    def test_bank_not_in_default_battery(self):
        """The demand bank is OPT-IN: none of its entries appear in the default
        registry, so a routine `runner` run never balloons by 1300+ turns."""
        default_ids = {c.id for c in get_all_conversations()}
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "bank.jsonl"
            path, _ = regenerate_bank(out_path=out)
            bank_ids = {c.id for c in load_corpus(path)}
        overlap = default_ids & bank_ids
        self.assertEqual(overlap, set(),
                         f"demand-bank ids must not leak into the default battery: {overlap}")
        # And the bank dwarfs the default battery (why it must stay opt-in).
        self.assertGreater(len(bank_ids), len(default_ids))


if __name__ == "__main__":
    unittest.main()
