# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""WP-4.2 — coverage-gap burndown gate (the ratchet).

The design's done-line is "the coverage report shows no un-annotated gap." That
is reached by a monotonic burndown, and this gate is what enforces it: a tracked
backlog (coverage_backlog.json) lists every un-annotated (testable=yes) inventory
cell not yet asserted by a scenario, and the gate holds two invariants —

  * NO NEW GAP: every current un-annotated gap must already be in the backlog, so
    a change that stops covering a cell (or adds an un-annotated capability) fails
    CI instead of silently widening the hole.
  * ONLY SHRINKS: every backlog cell must still be a real gap; the moment a
    scenario covers one, its stale backlog entry fails, forcing removal — the
    backlog can never grow and a covered cell can never linger as "known".

Most remaining cells are live-capture-dependent (a route source / gate outcome
needs a real daemon trace to grade), so the bulk close as the discovery->promotion
engine (WP-3.3) + live runs feed recorded captures; this gate makes that burndown
measurable and regression-proof.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from intergen.tests.scenario.inventory import coverage_report
from intergen.tests.scenario.loader import load_scenarios

_SEEDS = Path(__file__).resolve().parent / "scenario" / "seeds"
_BACKLOG = Path(__file__).resolve().parent / "scenario" / "coverage_backlog.json"


def _current_gaps() -> set[str]:
    seeds = load_scenarios(_SEEDS)
    return {f"{g.registry}:{g.row}"
            for g in coverage_report(seeds).gaps if g.testable == "yes"}


def _backlog() -> dict:
    return json.loads(_BACKLOG.read_text())


class CoverageGateTests(unittest.TestCase):
    def test_no_new_un_annotated_gap(self):
        current = _current_gaps()
        backlog = set(_backlog()["cells"])
        new_gaps = current - backlog
        self.assertEqual(new_gaps, set(),
                         f"NEW un-annotated coverage gap(s) not in the backlog: "
                         f"{sorted(new_gaps)} — cover them or annotate the cell.")

    def test_backlog_only_shrinks_no_stale_entries(self):
        current = _current_gaps()
        backlog = set(_backlog()["cells"])
        covered_but_still_listed = backlog - current
        self.assertEqual(covered_but_still_listed, set(),
                         f"backlog lists cell(s) that are now COVERED: "
                         f"{sorted(covered_but_still_listed)} — remove them from "
                         "coverage_backlog.json (the ratchet only shrinks).")

    def test_backlog_count_matches_its_list(self):
        b = _backlog()
        self.assertEqual(b["count"], len(b["cells"]),
                         "coverage_backlog.json count field is out of sync with cells")

    def test_backlog_cells_are_well_formed(self):
        for cell in _backlog()["cells"]:
            self.assertRegex(cell, r"^[A-F]:.+",
                             f"backlog cell {cell!r} is not 'REGISTRY:row'")


if __name__ == "__main__":
    unittest.main()
