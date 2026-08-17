# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Per-tier decomposition-cap validation hook (decomposer.validate_decomposition_cap).

The per-tier action-sizing caps (_TIER_THRESHOLDS) are diagnostic-only: they do
NOT gate the split. This hook lets those caps be validated against real traces
without changing behavior — with enforcing=False (the only mode wired today) it
only reports whether a split exceeds the tier cap. These cases pin the hook's
report and confirm analyze_query's split decision is unchanged by it.
"""
import unittest

from intergen.decomposer import (
    analyze_query,
    validate_decomposition_cap,
    DecompositionCapCheck,
    _TIER_THRESHOLDS,
)
from intergen.interfaces.types import HardwareTierLevel


class CapHookTest(unittest.TestCase):
    def test_under_cap_not_flagged(self):
        chk = validate_decomposition_cap(["a", "b"], HardwareTierLevel.TIER_3)
        self.assertIsInstance(chk, DecompositionCapCheck)
        self.assertEqual(chk.tier_cap, _TIER_THRESHOLDS[HardwareTierLevel.TIER_3])
        self.assertEqual(chk.split_count, 2)
        self.assertFalse(chk.over_cap)
        self.assertFalse(chk.enforcing)

    def test_over_cap_flagged_for_2b(self):
        # TIER_1 cap is 1; a two-way split is over cap.
        chk = validate_decomposition_cap(["x", "y"], HardwareTierLevel.TIER_1)
        self.assertEqual(chk.tier_cap, 1)
        self.assertTrue(chk.over_cap)

    def test_at_cap_boundary_not_over(self):
        chk = validate_decomposition_cap(["a", "b", "c"], HardwareTierLevel.TIER_2)
        self.assertEqual(chk.tier_cap, 3)
        self.assertFalse(chk.over_cap)

    def test_enforcing_flag_is_recorded_only(self):
        chk = validate_decomposition_cap(
            ["a", "b", "c", "d"], HardwareTierLevel.TIER_2, enforcing=True
        )
        self.assertTrue(chk.over_cap)
        self.assertTrue(chk.enforcing)

    def test_hook_does_not_change_split_decision(self):
        # A genuine two-part compound still decomposes on the 2B (cap=1) — the
        # hook observes over_cap but the split is ungated, exactly as before.
        q = "what's my hostname and what year was Linux created"
        res_2b = analyze_query(q, HardwareTierLevel.TIER_1)
        res_9b = analyze_query(q, HardwareTierLevel.TIER_2)
        self.assertTrue(res_2b.needs_decomposition)
        self.assertEqual(
            res_2b.needs_decomposition, res_9b.needs_decomposition
        )
        self.assertEqual(res_2b.sub_queries, res_9b.sub_queries)


if __name__ == "__main__":
    unittest.main()
