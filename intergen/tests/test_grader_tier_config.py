# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Per-tier AUTO_ASSERTION_CONFIG skeleton (grader.grade_turn).

The auto:* quality battery was shaped for the 2B; a larger model earns each
guard back by ablation rather than inheriting the full suite. AUTO_ASSERTION_CONFIG
keys the battery by tier: TIER_1/TIER_2 hold the full set (behavior-preserving),
TIER_3 (the 35B) is intentionally empty per Baseline-B. grade_turn only consults
it when a tier is passed; the default (tier=None) is unchanged.
"""
import unittest

from intergen.tests.grader import (
    grade_turn,
    AUTO_ASSERTION_CONFIG,
    _ALL_AUTO_ASSERTIONS,
)
from intergen.interfaces.types import HardwareTierLevel


def _auto_types(results):
    return {r.type for r in results if r.type.startswith("auto:")}


class TierConfigTest(unittest.TestCase):
    # A response that trips several auto:* guards (filler open, empty-ish),
    # graded with NO declared assertions so only auto:* results appear.
    RESPONSE = {"text": "Sure! I can help with that.", "source": "llm_freeform",
                "tool_calls": [], "handled": True}

    def test_default_tier_none_emits_full_auto_battery(self):
        results = grade_turn(self.RESPONSE, [])
        self.assertEqual(_auto_types(results), set(_ALL_AUTO_ASSERTIONS))

    def test_tier1_full_battery_matches_default(self):
        default = grade_turn(self.RESPONSE, [])
        tier1 = grade_turn(self.RESPONSE, [], tier=HardwareTierLevel.TIER_1)
        self.assertEqual(_auto_types(default), _auto_types(tier1))

    def test_tier2_full_battery(self):
        results = grade_turn(self.RESPONSE, [], tier=HardwareTierLevel.TIER_2)
        self.assertEqual(_auto_types(results), set(_ALL_AUTO_ASSERTIONS))

    def test_tier3_sheds_all_auto_assertions(self):
        results = grade_turn(self.RESPONSE, [], tier=HardwareTierLevel.TIER_3)
        self.assertEqual(_auto_types(results), set())

    def test_tier3_still_grades_declared_assertions(self):
        # A declared (non-auto) assertion is NEVER filtered, even at TIER_3.
        from intergen.tests.conversations import Assertion
        declared = [Assertion("contains", "help", "must acknowledge")]
        results = grade_turn(self.RESPONSE, declared,
                             tier=HardwareTierLevel.TIER_3)
        types = {r.type for r in results}
        self.assertIn("contains", types)
        self.assertEqual(_auto_types(results), set())

    def test_config_shape(self):
        self.assertEqual(
            AUTO_ASSERTION_CONFIG[HardwareTierLevel.TIER_1], _ALL_AUTO_ASSERTIONS)
        self.assertEqual(
            AUTO_ASSERTION_CONFIG[HardwareTierLevel.TIER_2], _ALL_AUTO_ASSERTIONS)
        self.assertEqual(AUTO_ASSERTION_CONFIG[HardwareTierLevel.TIER_3], ())
        self.assertEqual(len(_ALL_AUTO_ASSERTIONS), 12)


if __name__ == "__main__":
    unittest.main()
