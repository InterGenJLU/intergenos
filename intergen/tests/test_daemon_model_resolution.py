# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Regression: `intergen setup` and the daemon MUST resolve the model identically.

The ge9b-01 §8-eval finding: on a fresh install the panel icon never appeared
because the engine never started. `intergen setup` resolved the model from the
DETECTOR's recommendation (an integrated-GPU Tier-2 box gets the 2B for latency)
and onboarding downloaded the 2B; the daemon instead resolved
`get_model_for_tier(resolved_tier)` = the 9B, found it absent, logged "No model
downloaded", and the icon gate (correctly) hid the panel icon. Each file's comment
claimed to mirror the other; they did opposites.

The fix (operator framework 2026-07-11, tiering is DATA-DECIDED): ONE shared
resolution path — `ModelManager.resolve_for_detected` — used by BOTH, and the
dispatch lane DERIVES FROM the resolved model (`resolve_dispatch_for_model`),
never the raw hardware tier. These tests pin both halves and the exact dead-end
state (2B on disk, Tier-2 detected → engine can start).
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from intergen.dispatch_policy import (
    DispatchMode, FLOOR_TIER, resolve_dispatch_for_model,
)
from intergen.interfaces.types import HardwareTierLevel
from intergen.model_manager import ModelManager

# The real catalog names/filenames the resolver keys on.
NAME_2B = "InternVL3.5-2B"
FILE_2B = "OpenGVLab_InternVL3_5-2B-Q4_K_M.gguf"
NAME_9B = "Qwen3.5-9B"
FILE_9B = "Qwen3.5-9B-intergen-round3-Q4_K_M.gguf"
NAME_35B = "Qwen3.5-35B-A3B"


def _tier(level: HardwareTierLevel, recommended: str) -> SimpleNamespace:
    """A minimal detector result — the two fields resolve_for_detected reads."""
    return SimpleNamespace(tier=level, recommended_model=recommended)


class _MMHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Path(self._tmp.name) / "llm"
        self.store.mkdir(parents=True)
        self.mm = ModelManager(model_dir=self.store,
                               manifest_path=Path(self._tmp.name) / "m.json")
        # Hermetic pin state: the 2B and 9B pinned, the 35B UNPINNED — the
        # pre-35B-pin posture (any install whose shipped manifest predates the
        # 35B entry) so the unpinned-35B recommendation caps to the 9B.
        self.mm._pins = {FILE_2B: "a" * 64, FILE_9B: "b" * 64}

    def tearDown(self):
        self._tmp.cleanup()

    def _place(self, filename: str) -> None:
        (self.store / filename).write_bytes(b"GGUF-fake" * 8)


class TestSharedModelResolution(_MMHarness):
    def test_igpu_tier2_resolves_2b_not_9b(self):
        # THE ge9b-01 divergence, daemon-side: an integrated-GPU Tier-2 box (the
        # detector recommends the 2B) must resolve the 2B — NOT the 9B a bare
        # tier lookup would pick.
        m = self.mm.resolve_for_detected(_tier(HardwareTierLevel.TIER_2, NAME_2B))
        self.assertIsNotNone(m)
        self.assertEqual(m.name, NAME_2B)
        self.assertEqual(m.tier, HardwareTierLevel.TIER_1)
        # The old daemon path — proving the two genuinely diverged.
        self.assertEqual(
            self.mm.get_model_for_tier(HardwareTierLevel.TIER_2).name, NAME_9B)

    def test_igpu_2b_on_disk_engine_can_start(self):
        # THE regression fixture (item 4): the exact dead-end state — the 2B is
        # ON DISK (onboarding downloaded it), the 9B is ABSENT, the box detects
        # Tier-2. The resolver must return the downloaded 2B so the engine starts,
        # instead of the absent 9B (downloaded=False) that hid the panel icon.
        self._place(FILE_2B)
        m = self.mm.resolve_for_detected(_tier(HardwareTierLevel.TIER_2, NAME_2B))
        self.assertEqual(m.name, NAME_2B)
        self.assertTrue(m.downloaded)          # engine has a model to load
        # The old pick would have dead-ended: the 9B is not on disk.
        self.assertFalse(
            self.mm.get_model_for_tier(HardwareTierLevel.TIER_2).downloaded)

    def test_dgpu_tier2_resolves_9b(self):
        # A DISCRETE-GPU Tier-2 box: the detector recommends the 9B → resolve it.
        m = self.mm.resolve_for_detected(_tier(HardwareTierLevel.TIER_2, NAME_9B))
        self.assertEqual(m.name, NAME_9B)
        self.assertEqual(m.tier, HardwareTierLevel.TIER_2)

    def test_dgpu_tier3_35b_recommendation_caps_to_9b(self):
        # The PI-Z13 case, preserved: a Tier-3 dGPU box recommends the unpinned
        # 35B; the shared path caps it to the pinned 9B (the development machine posture) — NOT a
        # dead-end on the un-downloadable 35B.
        m = self.mm.resolve_for_detected(_tier(HardwareTierLevel.TIER_3, NAME_35B))
        self.assertEqual(m.name, NAME_9B)
        self.assertEqual(m.tier, HardwareTierLevel.TIER_2)

    def test_unknown_recommendation_falls_back_to_tier_lookup(self):
        # A recommendation name the catalog doesn't know → fall back to the bare
        # tier lookup (never return None and dead-end the daemon).
        m = self.mm.resolve_for_detected(
            _tier(HardwareTierLevel.TIER_2, "no-such-model-9000"))
        self.assertEqual(m.name, NAME_9B)      # get_model_for_tier(TIER_2)


class TestModelFirstDispatchLane(_MMHarness):
    """The lane DERIVES FROM the resolved model — the second half of the fix."""

    def test_igpu_2b_model_gives_locked_floor(self):
        # Resolve the model first (iGPU T2 → 2B), then the lane from it: LOCKED.
        m = self.mm.resolve_for_detected(_tier(HardwareTierLevel.TIER_2, NAME_2B))
        r = resolve_dispatch_for_model(
            m.tier, detected_tier=HardwareTierLevel.TIER_2)
        self.assertEqual(r.tier, FLOOR_TIER)
        self.assertEqual(r.dispatch_mode, DispatchMode.LOCKED_DOWN)
        self.assertTrue(r.lock_dispatch)
        self.assertFalse(r.fell_back_to_floor)   # the 2B floor is not a fallback
        self.assertEqual(r.detected_tier, HardwareTierLevel.TIER_2)

    def test_dgpu_9b_model_gives_native(self):
        # Resolve the model first (dGPU T3 → capped 9B), then the lane: NATIVE.
        m = self.mm.resolve_for_detected(_tier(HardwareTierLevel.TIER_3, NAME_35B))
        r = resolve_dispatch_for_model(
            m.tier, detected_tier=HardwareTierLevel.TIER_3)
        self.assertEqual(r.tier, HardwareTierLevel.TIER_2)
        self.assertEqual(r.dispatch_mode, DispatchMode.NATIVE)
        self.assertFalse(r.lock_dispatch)


if __name__ == "__main__":
    unittest.main()
