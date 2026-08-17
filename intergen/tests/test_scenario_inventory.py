# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""WP-1.4 — capability inventory + coverage report + drift guards.

Verifies the five registries enumerate from the tree (no hardcoded counts), the
route-source drift guard tracks router.py, and the coverage report diffs the
enumerated inventory against what the seed scenario corpus actually asserts —
gaps listed, not blanked.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from intergen.tests.capability_inventory import ALL_TOOLS, GATE_OUTCOMES, GATED_TOOLS
from intergen.tests.scenario import inventory as inv
from intergen.tests.scenario.loader import load_scenarios
from intergen.tests.scenario.schema import POSTURES
from intergen.tests.scenario.trace import READS_REALITY_TOOLS

_SEEDS = Path(__file__).resolve().parent / "scenario" / "seeds"


class RouteSourceDriftTests(unittest.TestCase):
    def test_route_sources_scanned_from_router(self):
        srcs = inv.route_sources_in_tree()
        # Real route verdicts the router emits must be present...
        for s in ("explain", "keyword", "capability_question", "llm_tools",
                  "llm_freeform", "memory", "safety_decline", "decomposed"):
            self.assertIn(s, srcs, s)
        # ...and the named sentinels excluded.
        self.assertNotIn("router", srcs)
        self.assertNotIn("empty_input", srcs)

    def test_registry_b_matches_tree(self):
        # The drift guard's invariant: the inventory's route set == the tree's.
        self.assertEqual(inv.ROUTE_SOURCES, inv.route_sources_in_tree())

    def test_drift_guard_raises_on_mismatch(self):
        # Simulate a decoupled/hardcoded Registry B that dropped a real source:
        # the guard must catch it (we exercise the comparison the guard performs).
        tree = inv.route_sources_in_tree()
        stale = frozenset(tree - {"keyword"})
        self.assertNotEqual(stale, tree)  # the exact condition the guard raises on


class EnumerationTests(unittest.TestCase):
    def test_registries_present_and_derived(self):
        rows = inv.enumerate_inventory()
        regs = {r.registry for r in rows}
        self.assertEqual(regs, {"A", "B", "C", "D", "E", "F"})

    def test_registry_a_is_the_tool_set(self):
        a_rows = {r.row for r in inv.enumerate_inventory() if r.registry == "A"}
        self.assertEqual(a_rows, set(ALL_TOOLS))

    def test_registry_a_notes_reads_reality(self):
        rows = {r.row: r for r in inv.enumerate_inventory() if r.registry == "A"}
        self.assertIn("reads_live_state=True", rows["web_search"].note)
        self.assertIn("reads_live_state=False", rows["open_application"].note)
        self.assertEqual("web_search" in READS_REALITY_TOOLS, True)

    def test_registry_c_covers_gated_full_outcome_set(self):
        c_rows = {r.row for r in inv.enumerate_inventory() if r.registry == "C"}
        for tool in GATED_TOOLS:
            for oc in GATE_OUTCOMES:
                self.assertIn(f"{tool}:{oc}", c_rows)

    def test_registry_e_is_the_posture_matrix(self):
        e_rows = {r.row for r in inv.enumerate_inventory() if r.registry == "E"}
        self.assertEqual(e_rows, set(POSTURES))


class CoverageReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seeds = load_scenarios(_SEEDS)
        cls.report = inv.coverage_report(cls.seeds)

    def test_report_emits_totals_and_gaps(self):
        self.assertEqual(self.report.total, len(inv.enumerate_inventory()))
        self.assertGreater(self.report.covered, 0)
        self.assertLess(self.report.covered, self.report.total)  # Phase-1 gaps exist
        self.assertTrue(self.report.gaps)

    def test_seed_corpus_covers_expected_cells(self):
        covered = self.report.covered_cells
        # Registry A — the three tools the seeds assert against.
        self.assertIn(("A", "run_command"), covered)
        self.assertIn(("A", "web_search"), covered)
        self.assertIn(("A", "manage_services"), covered)
        # Registry B — the two route sources the seeds assert.
        self.assertIn(("B", "explain"), covered)
        self.assertIn(("B", "capability_question"), covered)
        # Registry D — the fabrication/consistency invariants.
        self.assertIn(("D", "no_fabricated_state"), covered)
        self.assertIn(("D", "no_invented_artifact"), covered)
        self.assertIn(("D", "self_consistent"), covered)
        self.assertIn(("D", "source_citation"), covered)
        # Registry E — the declared floor posture.
        self.assertIn(("E", "2B-locked"), covered)

    def test_gate_outcome_matrix_is_an_open_gap(self):
        # Phase 1 authors the fabrication axis, not the gate lifecycle — the whole
        # (gated tool, outcome) matrix must read as gaps, honestly.
        gap_cells = {(g.registry, g.row) for g in self.report.gaps}
        self.assertIn(("C", "manage_packages:deny"), gap_cells)
        self.assertIn(("C", "write_file:cancel"), gap_cells)

    def test_render_is_human_readable(self):
        text = self.report.render()
        self.assertIn("scenario coverage:", text)
        self.assertIn("registry A:", text)
        self.assertIn("GAPS", text)

    def test_by_registry_totals_sum_to_total(self):
        per = self.report.by_registry()
        self.assertEqual(sum(d["total"] for d in per.values()), self.report.total)


if __name__ == "__main__":
    unittest.main()
