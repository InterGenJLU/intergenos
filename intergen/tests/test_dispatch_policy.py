# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Tests for the tier resolver / dispatch-mode policy (the 2B lockdown).

Pins the fail-closed invariant: the 2B (Tier-1) is the verified-everywhere
default; a bigger tier resolves — with native dispatch — to the LARGEST shipped
lane at or below the detected tier (walk-down); when no shipped lane sits at or
below it (or detection is inconclusive) it resolves to the 2B floor + locked-down
dispatch. Model and dispatch always move together (no drift).
"""
from __future__ import annotations

import unittest

from intergen.dispatch_policy import (
    DispatchMode,
    SHIPPED_LOGIC_LANES,
    FLOOR_TIER,
    is_system_category_conversation,
    resolve_dispatch,
    resolve_dispatch_for_model,
)
from intergen.interfaces.types import HardwareTier, HardwareTierLevel


def _hw(tier: HardwareTierLevel) -> HardwareTier:
    """A minimal HardwareTier carrying the detected tier (the only field the
    resolver reads). The model/quant fields are irrelevant to resolution."""
    return HardwareTier(
        ram_gb=8.0,
        gpu_vendor=None,
        gpu_model=None,
        gpu_vram_mb=None,
        tier=tier,
        recommended_model="InternVL3.5-2B",
        recommended_quant="Q4_K_M",
        estimated_model_size_gb=1.2,
    )


class DispatchPolicyTests(unittest.TestCase):
    # ── the shipped-today truth ───────────────────────────────────────────────
    def test_default_lane_set_ships_the_9b_lane(self):
        # The build ships the 9B (TIER_2) native-dispatch lane as of ge9b-01
        # (decided). The 2B (TIER_1) stays the locked floor; the 35B
        # (TIER_3) lane is NOT yet shipped. This default is load-bearing — any
        # further lane change is a deliberate code change that updates it here in
        # the same motion.
        self.assertEqual(SHIPPED_LOGIC_LANES,
                         frozenset({HardwareTierLevel.TIER_2}))
        self.assertNotIn(HardwareTierLevel.TIER_3, SHIPPED_LOGIC_LANES)
        self.assertEqual(FLOOR_TIER, HardwareTierLevel.TIER_1)

    # ── the floor ─────────────────────────────────────────────────────────────
    def test_tier1_resolves_to_locked_floor(self):
        r = resolve_dispatch(_hw(HardwareTierLevel.TIER_1))
        self.assertEqual(r.tier, HardwareTierLevel.TIER_1)
        self.assertEqual(r.dispatch_mode, DispatchMode.LOCKED_DOWN)
        self.assertTrue(r.lock_dispatch)
        self.assertFalse(r.fell_back_to_floor)
        self.assertEqual(r.detected_tier, HardwareTierLevel.TIER_1)

    # ── fail-closed: capable hardware, no shipped lane → fall back to 2B ───────
    def test_tier2_with_empty_lanes_falls_back_to_locked_floor(self):
        # The fail-closed mechanism itself, independent of the shipped default:
        # a TIER_2 box whose lane is NOT shipped falls back to the locked 2B.
        r = resolve_dispatch(
            _hw(HardwareTierLevel.TIER_2),
            shipped_lanes=frozenset(),
        )
        self.assertEqual(r.tier, HardwareTierLevel.TIER_1)
        self.assertEqual(r.dispatch_mode, DispatchMode.LOCKED_DOWN)
        self.assertTrue(r.lock_dispatch)
        self.assertTrue(r.fell_back_to_floor)
        self.assertEqual(r.detected_tier, HardwareTierLevel.TIER_2)

    # ── the shipped default now HONORS the 9B + unlocks native ────────────────
    def test_tier2_ships_by_default_unlocks_native(self):
        # With the SHIPPED default (the 9B lane ships), a 9B-capable box is
        # honored and native dispatch is unlocked — the ge9b-01 posture. An EXACT
        # lane match is not a walk-down.
        r = resolve_dispatch(_hw(HardwareTierLevel.TIER_2))
        self.assertEqual(r.tier, HardwareTierLevel.TIER_2)
        self.assertEqual(r.dispatch_mode, DispatchMode.NATIVE)
        self.assertFalse(r.lock_dispatch)
        self.assertFalse(r.fell_back_to_floor)
        self.assertFalse(r.walked_down)

    def test_tier3_detected_walks_down_to_shipped_9b_lane(self):
        # THE ge9b-01 walk-down (decided 2026-07-09): a Tier-3 box (16GB+
        # dGPU — the class the Zephyrus itself detects as) with the DEFAULT lane
        # set (only the 9B/TIER_2 shipped) does NOT floor-clamp; it runs the
        # largest shipped lane at or below it — the 9B — with native dispatch.
        r = resolve_dispatch(_hw(HardwareTierLevel.TIER_3))
        self.assertEqual(r.tier, HardwareTierLevel.TIER_2)
        self.assertEqual(r.dispatch_mode, DispatchMode.NATIVE)
        self.assertFalse(r.lock_dispatch)
        self.assertFalse(r.fell_back_to_floor)
        self.assertTrue(r.walked_down)                  # provenance: T3 → T2
        self.assertEqual(r.detected_tier, HardwareTierLevel.TIER_3)

    def test_tier3_detected_with_empty_lanes_falls_back_to_locked_floor(self):
        # No lane shipped at or below a Tier-3 candidate → the locked 2B floor
        # (the fail-closed default holds when the walk-down finds nothing).
        r = resolve_dispatch(
            _hw(HardwareTierLevel.TIER_3),
            shipped_lanes=frozenset(),
        )
        self.assertEqual(r.tier, HardwareTierLevel.TIER_1)
        self.assertEqual(r.dispatch_mode, DispatchMode.LOCKED_DOWN)
        self.assertTrue(r.lock_dispatch)
        self.assertTrue(r.fell_back_to_floor)
        self.assertFalse(r.walked_down)

    # ── bigger tier honored + native unlocked ONCE its lane ships ─────────────
    def test_tier2_with_shipped_lane_unlocks_native(self):
        r = resolve_dispatch(
            _hw(HardwareTierLevel.TIER_2),
            shipped_lanes=frozenset({HardwareTierLevel.TIER_2}),
        )
        self.assertEqual(r.tier, HardwareTierLevel.TIER_2)
        self.assertEqual(r.dispatch_mode, DispatchMode.NATIVE)
        self.assertFalse(r.lock_dispatch)
        self.assertFalse(r.fell_back_to_floor)

    def test_tier3_with_shipped_lane_unlocks_native(self):
        # Both lanes shipped → the Tier-3 box runs its own lane (an exact match at
        # the top of the walk-down, not a walk-down).
        r = resolve_dispatch(
            _hw(HardwareTierLevel.TIER_3),
            shipped_lanes=frozenset(
                {HardwareTierLevel.TIER_2, HardwareTierLevel.TIER_3}),
        )
        self.assertEqual(r.tier, HardwareTierLevel.TIER_3)
        self.assertEqual(r.dispatch_mode, DispatchMode.NATIVE)
        self.assertFalse(r.lock_dispatch)
        self.assertFalse(r.walked_down)

    def test_tier3_capable_only_tier2_lane_shipped_walks_down_to_9b(self):
        # The 9B lane shipped, the 35B lane did not — a Tier-3 box runs the 9B,
        # the largest shipped lane AT OR BELOW it, with native dispatch. Operator
        # Decided 2026-07-09: walk-down, NOT floor-clamp — the 9B ships to 16GB+
        # boxes, not only to the 8-15GB band. (This case previously asserted a
        # floor-clamp; that decision inverted it.)
        r = resolve_dispatch(
            _hw(HardwareTierLevel.TIER_3),
            shipped_lanes=frozenset({HardwareTierLevel.TIER_2}),
        )
        self.assertEqual(r.tier, HardwareTierLevel.TIER_2)
        self.assertEqual(r.dispatch_mode, DispatchMode.NATIVE)
        self.assertFalse(r.lock_dispatch)
        self.assertFalse(r.fell_back_to_floor)
        self.assertTrue(r.walked_down)

    # ── manual override sits on top ───────────────────────────────────────────
    def test_override_to_floor_on_capable_box_locks(self):
        # Operator forces the 2B on a 9B-capable box (even if the 9B lane ships).
        r = resolve_dispatch(
            _hw(HardwareTierLevel.TIER_2),
            shipped_lanes=frozenset({HardwareTierLevel.TIER_2}),
            override_tier=HardwareTierLevel.TIER_1,
        )
        self.assertEqual(r.tier, HardwareTierLevel.TIER_1)
        self.assertEqual(r.dispatch_mode, DispatchMode.LOCKED_DOWN)
        self.assertEqual(r.override_tier, HardwareTierLevel.TIER_1)

    def test_override_to_bigger_with_lane_unlocks(self):
        # Operator forces the 9B on a box hardware-detected as Tier-1, and the
        # 9B lane ships → honored + native unlocked (the operator's footgun call).
        r = resolve_dispatch(
            _hw(HardwareTierLevel.TIER_1),
            shipped_lanes=frozenset({HardwareTierLevel.TIER_2}),
            override_tier=HardwareTierLevel.TIER_2,
        )
        self.assertEqual(r.tier, HardwareTierLevel.TIER_2)
        self.assertEqual(r.dispatch_mode, DispatchMode.NATIVE)
        self.assertEqual(r.detected_tier, HardwareTierLevel.TIER_1)

    def test_override_to_tier3_walks_down_to_shipped_9b(self):
        # Operator forces the 35B on a Tier-1 box; only the 9B lane ships → the
        # override walks DOWN to the 9B + native (same walk-down as the detected
        # path — the override just supplies the candidate).
        r = resolve_dispatch(
            _hw(HardwareTierLevel.TIER_1),
            shipped_lanes=frozenset({HardwareTierLevel.TIER_2}),
            override_tier=HardwareTierLevel.TIER_3,
        )
        self.assertEqual(r.tier, HardwareTierLevel.TIER_2)
        self.assertEqual(r.dispatch_mode, DispatchMode.NATIVE)
        self.assertTrue(r.walked_down)
        self.assertEqual(r.detected_tier, HardwareTierLevel.TIER_1)
        self.assertEqual(r.override_tier, HardwareTierLevel.TIER_3)

    def test_override_to_bigger_without_lane_still_fails_closed(self):
        # An override CANNOT conjure a logic lane that isn't in the build — even a
        # forced bigger tier falls back to the locked 2B floor when its lane is
        # absent (you can't run native-dispatch code that doesn't exist).
        r = resolve_dispatch(
            _hw(HardwareTierLevel.TIER_1),
            shipped_lanes=frozenset(),
            override_tier=HardwareTierLevel.TIER_3,
        )
        self.assertEqual(r.tier, HardwareTierLevel.TIER_1)
        self.assertTrue(r.lock_dispatch)
        self.assertTrue(r.fell_back_to_floor)

    # ── model + dispatch never drift ──────────────────────────────────────────
    def test_lock_dispatch_property_tracks_mode(self):
        locked = resolve_dispatch(_hw(HardwareTierLevel.TIER_1))
        native = resolve_dispatch(
            _hw(HardwareTierLevel.TIER_2),
            shipped_lanes=frozenset({HardwareTierLevel.TIER_2}))
        self.assertTrue(locked.lock_dispatch)
        self.assertEqual(locked.dispatch_mode, DispatchMode.LOCKED_DOWN)
        self.assertFalse(native.lock_dispatch)
        self.assertEqual(native.dispatch_mode, DispatchMode.NATIVE)


class DispatchForModelTests(unittest.TestCase):
    """The MODEL-FIRST resolver: the lane DERIVES FROM the resolved model.

    The counterpart consumed by the daemon after the shared model resolution
    (operator framework 2026-07-11: tiering is data-decided). It is keyed on the
    RESOLVED MODEL's tier, not the raw hardware tier — which is what closes the
    ge9b-01 iGPU-Tier-2 dead-end (2B model on 2-detected hardware → LOCKED, not
    the native 9B lane the box cannot serve).
    """

    def test_2b_model_locks_even_on_tier2_hardware(self):
        # The whole point: hardware detected Tier-2, but the resolved MODEL is the
        # 2B (the detector recommended it for latency) → LOCKED floor. Feeding the
        # raw Tier-2 to resolve_dispatch would (wrongly) unlock native.
        r = resolve_dispatch_for_model(
            HardwareTierLevel.TIER_1, detected_tier=HardwareTierLevel.TIER_2)
        self.assertEqual(r.tier, FLOOR_TIER)
        self.assertEqual(r.dispatch_mode, DispatchMode.LOCKED_DOWN)
        self.assertTrue(r.lock_dispatch)
        self.assertFalse(r.fell_back_to_floor)   # the 2B floor is not a fallback
        self.assertEqual(r.detected_tier, HardwareTierLevel.TIER_2)

    def test_9b_model_unlocks_native(self):
        r = resolve_dispatch_for_model(
            HardwareTierLevel.TIER_2, detected_tier=HardwareTierLevel.TIER_2)
        self.assertEqual(r.tier, HardwareTierLevel.TIER_2)
        self.assertEqual(r.dispatch_mode, DispatchMode.NATIVE)
        self.assertFalse(r.lock_dispatch)
        self.assertFalse(r.fell_back_to_floor)
        self.assertFalse(r.walked_down)

    def test_9b_model_from_tier3_hardware_is_native(self):
        # dGPU Tier-3 box whose 35B recommendation capped to the 9B: model tier is
        # TIER_2 → native, with the Tier-3 detected provenance preserved.
        r = resolve_dispatch_for_model(
            HardwareTierLevel.TIER_2, detected_tier=HardwareTierLevel.TIER_3)
        self.assertEqual(r.tier, HardwareTierLevel.TIER_2)
        self.assertEqual(r.dispatch_mode, DispatchMode.NATIVE)
        self.assertEqual(r.detected_tier, HardwareTierLevel.TIER_3)

    def test_override_may_lower_a_9b_box_to_locked(self):
        # Operator tightens: force the locked floor on a 9B box. Allowed (a
        # security tightening — you can always run the capable model locked).
        r = resolve_dispatch_for_model(
            HardwareTierLevel.TIER_2, detected_tier=HardwareTierLevel.TIER_2,
            override_tier=HardwareTierLevel.TIER_1)
        self.assertEqual(r.tier, FLOOR_TIER)
        self.assertEqual(r.dispatch_mode, DispatchMode.LOCKED_DOWN)
        self.assertEqual(r.override_tier, HardwareTierLevel.TIER_1)

    def test_override_cannot_raise_above_resolved_model(self):
        # THE invariant: "no box runs a model in a lane posture it was not
        # validated in." The resolved model is the 2B; an override to Tier-2
        # CANNOT conjure a native 9B lane — it stays LOCKED. (Contrast
        # resolve_dispatch, where an override may raise the tier — because there
        # the tier IS the model; here the model is already decided.)
        r = resolve_dispatch_for_model(
            HardwareTierLevel.TIER_1, detected_tier=HardwareTierLevel.TIER_2,
            override_tier=HardwareTierLevel.TIER_2)
        self.assertEqual(r.tier, FLOOR_TIER)
        self.assertEqual(r.dispatch_mode, DispatchMode.LOCKED_DOWN)
        self.assertTrue(r.lock_dispatch)

    def test_9b_model_with_unshipped_lane_fails_closed(self):
        # Fail-closed: a 9B model whose lane is NOT in this build → locked floor.
        r = resolve_dispatch_for_model(
            HardwareTierLevel.TIER_2, detected_tier=HardwareTierLevel.TIER_2,
            shipped_lanes=frozenset())
        self.assertEqual(r.tier, FLOOR_TIER)
        self.assertEqual(r.dispatch_mode, DispatchMode.LOCKED_DOWN)
        self.assertTrue(r.fell_back_to_floor)


class SystemCategoryConversationTests(unittest.TestCase):
    """The locked-floor grounding surface (2026-07-14). System-administration /
    privilege / authorization-layer / self-capability turns must be recognised so
    the router grounds them, instead of the locked 2B fabricating capability-denial
    and `sudo` folklore. Grounded in a live 2026-07-13 test session where pressing
    on why an upgrade was gated produced "I can't run commands directly", "run it
    with sudo", and "the system is in a privileged mode"."""

    def test_recognises_system_category_turns(self):
        for q in ("I thought you were supposed to help me administer this system",
                  "can you upgrade the system for me?",
                  "so updating the system is blocked by the safety layer?",
                  "what does 'privileged state changing' mean?",
                  "do you need root access to do that?",
                  "why can't you just run it with sudo?",
                  "does this need elevated permissions?",
                  "can you manage this system or not?"):
            self.assertTrue(is_system_category_conversation(q), q)

    def test_neutral_on_ordinary_questions(self):
        for q in ("how do I lock my screen",
                  "what's a good markdown editor",
                  "help me plan meals for the week",
                  "how much disk space do I have",
                  "explain what a symlink is",
                  "write a haiku about autumn"):
            self.assertFalse(is_system_category_conversation(q), q)

    def test_empty_and_none_safe(self):
        self.assertFalse(is_system_category_conversation(""))
        self.assertFalse(is_system_category_conversation(None))


if __name__ == "__main__":
    unittest.main()
