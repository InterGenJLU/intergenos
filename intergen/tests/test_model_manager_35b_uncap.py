# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Tier-3 (35B-A3B) un-cap plumbing: pinning the filename is the ONLY step.

`_cap_unpinned_to_highest_pinned` caps an unpinned model down to the highest
pinned tier at-or-below it, so a box whose shipped manifest lacks the 35B pin
resolves Tier 3 to the 9B (test_daemon_model_resolution covers that posture;
the shipped manifest now carries the 35B pin, so a current install un-caps).
This test proves the resolution plumbing routes the 35B the instant its
filename carries a pin — nothing else in the resolver needs to change. It uses a
hermetic pin (not the real sha256) because it exercises the CAP decision, which
keys only on pin PRESENCE; the sha256 verify chain is covered by the existing
model_manager tests.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from intergen.interfaces.types import HardwareTierLevel
from intergen.model_manager import ModelManager

NAME_2B = "InternVL3.5-2B"
FILE_2B = "OpenGVLab_InternVL3_5-2B-Q4_K_M.gguf"
NAME_9B = "Qwen3.5-9B"
FILE_9B = "Qwen3.5-9B-intergen-round3-Q4_K_M.gguf"
NAME_35B = "Qwen3.5-35B-A3B"
FILE_35B = "Qwen3.5-35B-A3B-Q4_K_M.gguf"


def _tier(level: HardwareTierLevel, recommended: str) -> SimpleNamespace:
    return SimpleNamespace(tier=level, recommended_model=recommended)


class Tier3UncapTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        store = Path(self._tmp.name) / "llm"
        store.mkdir(parents=True)
        self.mm = ModelManager(
            model_dir=store, manifest_path=Path(self._tmp.name) / "m.json"
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_unpinned_35b_caps_to_9b_control(self):
        # The cap decision itself, driven by pin PRESENCE. This was production
        # truth when the file was written; the shipped manifest now pins all
        # three, so the case below is the one that matches a current install.
        self.mm._pins = {FILE_2B: "a" * 64, FILE_9B: "b" * 64}
        self.assertEqual(
            self.mm.get_model_for_tier(HardwareTierLevel.TIER_3).name, NAME_9B
        )
        self.assertEqual(
            self.mm.resolve_for_detected(
                _tier(HardwareTierLevel.TIER_3, NAME_35B)
            ).name,
            NAME_9B,
        )

    def test_pinned_35b_resolves_uncapped(self):
        # Add the 35B pin -> the SAME resolver now routes Tier 3 to the 35B.
        self.mm._pins = {FILE_2B: "a" * 64, FILE_9B: "b" * 64, FILE_35B: "c" * 64}
        by_tier = self.mm.get_model_for_tier(HardwareTierLevel.TIER_3)
        self.assertEqual(by_tier.name, NAME_35B)
        self.assertEqual(by_tier.tier, HardwareTierLevel.TIER_3)
        resolved = self.mm.resolve_for_detected(
            _tier(HardwareTierLevel.TIER_3, NAME_35B)
        )
        self.assertEqual(resolved.name, NAME_35B)
        self.assertEqual(resolved.tier, HardwareTierLevel.TIER_3)

    def test_pinned_35b_does_not_disturb_lower_tiers(self):
        self.mm._pins = {FILE_2B: "a" * 64, FILE_9B: "b" * 64, FILE_35B: "c" * 64}
        self.assertEqual(
            self.mm.get_model_for_tier(HardwareTierLevel.TIER_1).name, NAME_2B
        )
        self.assertEqual(
            self.mm.get_model_for_tier(HardwareTierLevel.TIER_2).name, NAME_9B
        )


if __name__ == "__main__":
    unittest.main()
