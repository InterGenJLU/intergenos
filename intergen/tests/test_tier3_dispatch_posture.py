# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""What a 35B-capable box is actually served, and in what dispatch posture.

Two mechanisms decide this and they are decided separately:

  * WHICH MODEL. ModelManager caps a model with no shipped pin down to the
    highest pinned tier at or below it. The shipped manifest now pins all three
    catalog models, so the cap does not fire and a Tier-3 box is served the
    35B itself.
  * WHICH LANE. dispatch_policy.SHIPPED_LOGIC_LANES holds the tiers whose
    native logic lane ships in this build. It holds TIER_2 only. The daemon
    derives its lane from the RESOLVED MODEL through
    resolve_dispatch_for_model(), and that function does not walk down: a
    candidate tier with no shipped lane goes straight to the locked 2B floor.

So the posture is a Tier-3 MODEL on the Tier-1 LOCKED lane. Measured on a live
install 2026-08-25: model Qwen3.5-35B-A3B (tier 3, not capped), lane TIER_1,
dispatch LOCKED_DOWN, fell_back_to_floor True, walked_down False.

That is not what the surrounding comments said. dispatch_policy's own header
described a 35B-capable box running "the 9B (the largest shipped lane at or
below it) with native dispatch" — true of resolve_dispatch(), the path the
daemon does NOT take, and measurably false of the path it does; the daemon's
own comment said "the 35B caps to it", which stopped being true when the 35B
was pinned. This file exists so the posture is pinned by a check rather than
by prose, and so the difference between the two resolvers stays visible.

Nothing here reads the installed manifest: the pins are set hermetically, which
is what makes the cases about the resolution rules rather than about whichever
manifest happens to be on the machine running them.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from intergen import dispatch_policy as dp
from intergen.dispatch_policy import DispatchMode
from intergen.interfaces.types import HardwareTier, HardwareTierLevel
from intergen.model_manager import ModelManager

FILE_2B = "OpenGVLab_InternVL3_5-2B-Q4_K_M.gguf"
FILE_9B = "Qwen3.5-9B-intergen-round3-Q4_K_M.gguf"
FILE_35B = "Qwen3.5-35B-A3B-Q4_K_M.gguf"
ALL_PINNED = {FILE_2B: "a" * 64, FILE_9B: "b" * 64, FILE_35B: "c" * 64}


def _detected(level: HardwareTierLevel, recommended: str) -> HardwareTier:
    return HardwareTier(
        ram_gb=128, gpu_vendor="probe", gpu_model="probe", gpu_vram_mb=24576,
        tier=level, recommended_model=recommended, recommended_quant="Q4_K_M",
        estimated_model_size_gb=22,
    )


class Tier3Posture(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        store = Path(self._tmp.name) / "llm"
        store.mkdir(parents=True)
        self.mm = ModelManager(
            model_dir=store, manifest_path=Path(self._tmp.name) / "m.json")
        self.mm._pins = dict(ALL_PINNED)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_tier3_box_is_served_the_35b_itself(self) -> None:
        served = self.mm.get_model_for_tier(HardwareTierLevel.TIER_3)
        self.assertEqual(served.name, "Qwen3.5-35B-A3B")
        self.assertEqual(served.tier, HardwareTierLevel.TIER_3)

    def test_the_daemons_resolver_puts_that_35b_on_the_locked_floor(self) -> None:
        """The lane the daemon derives, and every field of it."""
        served = self.mm.get_model_for_tier(HardwareTierLevel.TIER_3)
        r = dp.resolve_dispatch_for_model(
            served.tier, detected_tier=HardwareTierLevel.TIER_3)
        self.assertEqual(r.tier, dp.FLOOR_TIER)
        self.assertEqual(r.dispatch_mode, DispatchMode.LOCKED_DOWN)
        self.assertTrue(r.fell_back_to_floor)
        self.assertFalse(
            r.walked_down,
            "resolve_dispatch_for_model walked DOWN to a smaller shipped lane; "
            "it is written to floor a candidate with no shipped lane, and the "
            "posture this file pins depends on that")

    def test_the_other_resolver_walks_down_and_is_not_the_daemons(self) -> None:
        """resolve_dispatch, from raw hardware, DOES walk down to the 9B lane
        with native dispatch. Pinned here because the two answers differ for
        exactly this box, and prose describing the walk-down as the shipped
        behaviour is what this lane corrects."""
        r = dp.resolve_dispatch(
            _detected(HardwareTierLevel.TIER_3, "Qwen3.5-35B-A3B"))
        self.assertEqual(r.tier, HardwareTierLevel.TIER_2)
        self.assertEqual(r.dispatch_mode, DispatchMode.NATIVE)
        self.assertTrue(r.walked_down)
        self.assertFalse(r.fell_back_to_floor)

    def test_the_daemon_derives_its_lane_from_the_resolved_model(self) -> None:
        """The claim above is only worth pinning if the daemon really takes
        that path, so this reads the daemon's own source rather than trusting
        the comment beside it."""
        src = (Path(__file__).resolve().parents[1] / "dbus_daemon.py").read_text()
        self.assertIn("resolve_dispatch_for_model", src)
        self.assertNotIn(
            "from intergen.dispatch_policy import resolve_dispatch\n", src,
            "the daemon imports the raw-hardware resolver; the posture pinned "
            "here is the model-derived one")

    def test_the_lower_tiers_are_unaffected(self) -> None:
        for level, name, lane, mode in (
            (HardwareTierLevel.TIER_1, "InternVL3.5-2B",
             HardwareTierLevel.TIER_1, DispatchMode.LOCKED_DOWN),
            (HardwareTierLevel.TIER_2, "Qwen3.5-9B",
             HardwareTierLevel.TIER_2, DispatchMode.NATIVE),
        ):
            served = self.mm.get_model_for_tier(level)
            self.assertEqual(served.name, name)
            r = dp.resolve_dispatch_for_model(served.tier, detected_tier=level)
            self.assertEqual(r.tier, lane)
            self.assertEqual(r.dispatch_mode, mode)

    def test_the_35b_lane_is_not_shipped_which_is_why_the_floor_applies(self) -> None:
        """The one constant the whole posture turns on. If a 35B logic lane
        ever ships, this case fails and the file above it has to be rewritten
        rather than quietly drifting."""
        self.assertNotIn(HardwareTierLevel.TIER_3, dp.SHIPPED_LOGIC_LANES)
        self.assertIn(HardwareTierLevel.TIER_2, dp.SHIPPED_LOGIC_LANES)


if __name__ == "__main__":
    unittest.main()
