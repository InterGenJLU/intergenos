# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Phase-2 corpus load + shape gate (CUT-034).

The generated scenario corpus (intergen/tests/scenario/corpus/) is the Phase-2
demand corpus. This test is the HOST-SIDE proof the dispatch requires: every
scenario must LOAD + validate against the real loader (same path the live-run
harness uses) and every turn must carry at least one effective assertion (the
no-vacuous-turn invariant). Grading against live model responses is the
BuildVM/live-run leg; this gate proves the corpus is well-formed and
grader-parseable — a scenario that would not load in the harness fails here first.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from intergen.tests.scenario.loader import load_scenarios
from intergen.tests.scenario.schema import (
    AXES, ASSERTION_TYPES, effective_assertion_count,
)

_CORPUS = Path(__file__).resolve().parent / "scenario" / "corpus"


class Phase2CorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenarios = load_scenarios(_CORPUS)

    def test_corpus_is_substantial(self):
        # The Phase-2 generation cut targets a large graded bank; guard the floor
        # so a truncated/empty corpus fails loudly rather than passing vacuously.
        self.assertGreaterEqual(len(self.scenarios), 400,
                                f"corpus too small: {len(self.scenarios)}")

    def test_every_scenario_declares_a_known_axis(self):
        for s in self.scenarios:
            self.assertTrue(s.axis, f"{s.id}: empty axis")
            for a in s.axis:
                self.assertIn(a, AXES, f"{s.id}: unknown axis {a!r}")

    def test_no_vacuous_turn(self):
        # The loader already enforces this; asserting it here documents the
        # contract and catches a regression if the loader ever relaxes.
        for s in self.scenarios:
            for i, turn in enumerate(s.turns):
                self.assertGreater(
                    effective_assertion_count(turn, s.category), 0,
                    f"{s.id} turn {i}: zero effective assertions")

    def test_all_explicit_assertion_types_known(self):
        for s in self.scenarios:
            for i, turn in enumerate(s.turns):
                for a in turn.assertions:
                    self.assertIn(
                        a.type, ASSERTION_TYPES,
                        f"{s.id} turn {i}: unknown assertion type {a.type!r}")

    def test_ids_unique(self):
        # load_scenarios already rejects duplicate ids across the whole load;
        # this makes the guarantee explicit in the corpus's own test.
        ids = [s.id for s in self.scenarios]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
