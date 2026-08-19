"""Tier assignment — GPU + VRAM only, top-down (_assign_tier).

The 2026-07-24 design decision: system RAM is NEVER a tier input,
and unknown capability always fails DOWN, never up. A box without a discrete
GPU is the Tier-1 2B floor by construction (the CPU latency rule made
structural); a discrete card walks top-down against the per-tier VRAM fit
gates. The prior RAM-threshold table and the RAM-based Tier-3
"expert-offload" leg are removed — 35B-with-experts-in-RAM is an explicit
operator decision, never a detection inference. These cases pin all of it.
"""

import unittest

from intergen.hardware import (
    HardwareDetector,
    TIER2_VRAM_MB,
    TIER3_RESIDENT_VRAM_MB,
)
from intergen.interfaces.types import HardwareTierLevel


class VramOnlyTierAssignTest(unittest.TestCase):
    def setUp(self):
        self.det = HardwareDetector()

    def test_no_discrete_gpu_is_tier1_floor(self):
        # No discrete GPU → the 2B floor, full stop. VRAM value irrelevant.
        self.assertEqual(
            self.det._assign_tier(is_discrete=False),
            HardwareTierLevel.TIER_1,
        )
        self.assertEqual(
            self.det._assign_tier(is_discrete=False, gpu_vram_mb=32768),
            HardwareTierLevel.TIER_1,
        )

    def test_unknown_vram_fails_down_never_up(self):
        # A discrete-looking card whose VRAM cannot be read lands on the
        # floor — the fail-open-to-Tier-3 escape hatch is dead.
        self.assertEqual(
            self.det._assign_tier(is_discrete=True, gpu_vram_mb=None),
            HardwareTierLevel.TIER_1,
        )

    def test_resident_vram_assigns_tier3(self):
        # A 22 GB+ card holds the ~21 GB model resident → Tier 3.
        for vram in (TIER3_RESIDENT_VRAM_MB, 24576, 32768):
            self.assertEqual(
                self.det._assign_tier(is_discrete=True, gpu_vram_mb=vram),
                HardwareTierLevel.TIER_3, vram,
            )

    def test_20gb_card_is_tier2_not_tier3(self):
        # The dual-Radeon dev PC's 20 GB card: under the resident bar →
        # Tier 2 (the 9B), exactly per the hardware-tier map. No RAM leg can lift it.
        self.assertEqual(
            self.det._assign_tier(is_discrete=True, gpu_vram_mb=20464),
            HardwareTierLevel.TIER_2,
        )

    def test_8gb_card_is_tier2(self):
        # 8 GB class (RTX 3070 Ti / RX 7600) fits the 9B + projector + KV.
        for vram in (8192, 8175, TIER2_VRAM_MB):
            self.assertEqual(
                self.det._assign_tier(is_discrete=True, gpu_vram_mb=vram),
                HardwareTierLevel.TIER_2, vram,
            )

    def test_small_card_fails_down_to_tier1(self):
        # Under the 9B fit gate (relic / small cards) → the floor.
        for vram in (TIER2_VRAM_MB - 1, 6144, 4096, 1024):
            self.assertEqual(
                self.det._assign_tier(is_discrete=True, gpu_vram_mb=vram),
                HardwareTierLevel.TIER_1, vram,
            )

    def test_ram_is_not_an_input(self):
        # The signature itself enforces the design: no RAM parameter exists.
        import inspect
        params = inspect.signature(self.det._assign_tier).parameters
        self.assertNotIn("ram_gb", params)
        self.assertEqual(list(params), ["is_discrete", "gpu_vram_mb"])

    def test_ram_offload_leg_is_dead(self):
        # The RAM-based Tier-3 expert-offload constant is gone from the module.
        import intergen.hardware as hw
        self.assertFalse(hasattr(hw, "TIER3_OFFLOAD_RAM_GB"))
        self.assertFalse(hasattr(hw, "TIER_MODELS_CPU_ONLY"))


if __name__ == "__main__":
    unittest.main()
