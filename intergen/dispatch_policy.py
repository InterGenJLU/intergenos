# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Tier resolver + dispatch-mode policy for InterGen (the 2B dispatch lockdown).

A PURELY ADDITIVE layer on top of hardware detection (:mod:`intergen.hardware`):
hardware detection is UNCHANGED. This resolver runs AFTER it and answers one
extra question — *is the bigger-tier "logic lane" actually shipped in THIS
build's code?* — then sets BOTH the effective model tier AND the dispatch mode
together, from one place, so a model and its dispatch posture can never drift
apart.

THESIS (decided, 2026-06-30): the model NEVER touches dispatch or args.
Code owns 100% of tool calling — the decision, the arguments, the execution. The
model does language only (understand, teach from the curated corpus, synthesize
results). Trust lives in the code path, not in model behavior — which is dead-on
the supreme directives: a code path is verifiable + testable (no silent
failures), and a user can trust deterministic dispatch in a way they never can a
model's whim.

So the Tier-1 model (InternVL3.5-2B) runs **LOCKED-DOWN**: the native LLM
tool-decision path (router P3, ``tool_choice="auto"``) is OFF; only the
deterministic matcher (P1 keyword / P2 semantic) + the route-to-tools guard
dispatch, always with code-extracted arguments. A bigger tier (9B / 35B) may
unlock native dispatch — but ONLY once its logic lane is actually shipped in the
build (settled roadmap: 9B post-Zephyrus, 35B post-OS-swap).

Fail-closed: the 2B is the verified-everywhere default. A bigger tier resolves to
the LARGEST shipped logic lane AT OR BELOW the hardware-detected tier (a walk
down); when NO shipped lane sits at or below it — or detection is missing or
inconclusive — it resolves to the 2B floor + locked-down dispatch. An unshipped
lane never unlocks itself: resolution only ever walks DOWN to a lane that is
actually in this build.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from intergen.interfaces.types import HardwareTier, HardwareTierLevel


class DispatchMode(str, Enum):
    """Whether the model is allowed to *decide* a tool call.

    LOCKED_DOWN — native LLM tool-decision is OFF; code owns 100% of dispatch
    (the 2B). NATIVE — native LLM tool-decision is available (gateable on) for a
    bigger tier whose logic lane ships.
    """
    LOCKED_DOWN = "locked_down"
    NATIVE = "native"


# Tiers whose native-dispatch LOGIC LANE is implemented + SHIPPED in this build.
#
# The 9B (TIER_2) lane SHIPS as of the ge9b-01 candidate (decided,
# post-Zephyrus): on a 9B-capable box the 9B is honored AND native dispatch is
# unlocked (dispatch_mode=NATIVE). The 2B (TIER_1) remains the verified-
# everywhere locked-down floor. The 35B (TIER_3) lane is still settled roadmap,
# not yet written:
#   - add HardwareTierLevel.TIER_3 when the 35B logic lane lands (post-OS-swap)
#
# This constant is the SOURCE OF TRUTH for "is the lane shipped in THIS build's
# code" — landing a lane MEANS adding its tier here in the same change that adds
# the code, and a lane picked up here is picked up AUTOMATICALLY with ZERO
# changes to hardware detection.
#
# THE TWO RESOLVERS BELOW READ IT DIFFERENTLY, and the difference decides what a
# 35B-capable box does:
#   * resolve_dispatch(), from the RAW DETECTED tier, WALKS DOWN: it resolves to
#     the largest shipped lane at or below the candidate, so a detected TIER_3
#     lands on the 9B lane with native dispatch.
#   * resolve_dispatch_for_model(), from the RESOLVED MODEL's tier, does NOT
#     walk down: a candidate with no shipped lane goes straight to the locked 2B
#     floor, because a model may not run in a posture it was not validated in.
# THE DAEMON TAKES THE SECOND ONE. Measured on a live install 2026-08-25: a
# TIER_3 box is served the 35B model — every catalog model is pinned, so the
# model-side cap does not fire — and runs it on the TIER_1 LOCKED_DOWN lane,
# fell_back_to_floor True, walked_down False. The same build still floor-clamps
# every box in a 2B-only build, by the same rule.
# Pinned by intergen/tests/test_tier3_dispatch_posture.py.
SHIPPED_LOGIC_LANES: frozenset[HardwareTierLevel] = frozenset({HardwareTierLevel.TIER_2})

# The verified-everywhere floor: the 2B locked-down tier. It is always available
# and always locked-down — it IS the code-owned path (no native lane required;
# it is the locked path itself).
FLOOR_TIER: HardwareTierLevel = HardwareTierLevel.TIER_1


@dataclass(frozen=True)
class ResolvedDispatch:
    """The resolver's output: the effective tier + dispatch mode, set TOGETHER.

    There is no (bigger model + locked) or (floor model + native) state — model
    and dispatch always move as one, which is what makes drift impossible.
    """
    tier: HardwareTierLevel              # EFFECTIVE tier — the model that loads
    dispatch_mode: DispatchMode
    detected_tier: HardwareTierLevel     # what hardware alone selected
    override_tier: HardwareTierLevel | None  # operator override, if any
    fell_back_to_floor: bool             # no shipped lane at/below candidate → floor
    walked_down: bool = False            # candidate resolved DOWN to a smaller
                                         # shipped lane. resolve_dispatch does
                                         # this (detected TIER_3 → the shipped
                                         # 9B); resolve_dispatch_for_model, the
                                         # daemon's path, never does — it floors
                                         # instead, so this stays False there

    @property
    def lock_dispatch(self) -> bool:
        """True when the model must be kept out of dispatch (the 2B lockdown).

        The single boolean the router + tool registry consume to gate the native
        tool-decision path. Derived from ``dispatch_mode`` so it cannot disagree.
        """
        return self.dispatch_mode is DispatchMode.LOCKED_DOWN


def resolve_dispatch(
    detected: HardwareTier,
    *,
    shipped_lanes: frozenset[HardwareTierLevel] = SHIPPED_LOGIC_LANES,
    override_tier: HardwareTierLevel | None = None,
) -> ResolvedDispatch:
    """Resolve the effective tier + dispatch mode (the additive logic-lane check).

    Args:
        detected: the UNCHANGED hardware-detection result
            (:meth:`intergen.hardware.HardwareDetector.detect`).
        shipped_lanes: the tiers whose native-dispatch logic lane ships in this
            build. Defaults to the code constant :data:`SHIPPED_LOGIC_LANES`
            (the source of truth); a caller may pass a config-driven set.
        override_tier: an operator manual override sitting on top of detection.
            ``None`` = use the detected tier.

    Fail-closed to the 2B floor: a bigger tier (hardware-detected OR
    operator-overridden) resolves — with native dispatch — to the LARGEST shipped
    lane AT OR BELOW the candidate (a walk down: a 35B-capable box whose 35B lane
    is unshipped runs the shipped 9B). When NO shipped lane sits at or below the
    candidate — or detection is missing/inconclusive — it resolves to the floor
    (2B + locked-down dispatch). The floor itself is never a native lane. Model
    and dispatch move together.
    """
    detected_tier = detected.tier
    candidate = override_tier if override_tier is not None else detected_tier

    # The floor is always available + always locked-down.
    if candidate == FLOOR_TIER:
        return ResolvedDispatch(
            tier=FLOOR_TIER,
            dispatch_mode=DispatchMode.LOCKED_DOWN,
            detected_tier=detected_tier,
            override_tier=override_tier,
            fell_back_to_floor=False,
        )

    # A bigger-than-floor candidate: WALK DOWN to the LARGEST shipped lane at or
    # below it, and run that lane's model with native dispatch. (TIER_3 detected,
    # {TIER_2} shipped → resolve TIER_2 + NATIVE — the shipped 9B, not the locked
    # floor.) FLOOR_TIER is excluded from the walk-down candidates: the floor is
    # the code-owned locked path, never a native lane, so it can never be unlocked
    # this way even if it were (wrongly) listed in shipped_lanes — fail-closed.
    eligible = [
        lane for lane in shipped_lanes
        if lane != FLOOR_TIER and lane.value <= candidate.value
    ]
    if eligible:
        resolved_lane = max(eligible, key=lambda lane: lane.value)
        return ResolvedDispatch(
            tier=resolved_lane,
            dispatch_mode=DispatchMode.NATIVE,
            detected_tier=detected_tier,
            override_tier=override_tier,
            fell_back_to_floor=False,
            walked_down=(resolved_lane != candidate),
        )

    # No shipped lane at or below the candidate → fall back to the 2B floor +
    # locked-down dispatch (the verified-everywhere default).
    return ResolvedDispatch(
        tier=FLOOR_TIER,
        dispatch_mode=DispatchMode.LOCKED_DOWN,
        detected_tier=detected_tier,
        override_tier=override_tier,
        fell_back_to_floor=True,
    )


def resolve_dispatch_for_model(
    model_tier: HardwareTierLevel,
    *,
    detected_tier: HardwareTierLevel,
    shipped_lanes: frozenset[HardwareTierLevel] = SHIPPED_LOGIC_LANES,
    override_tier: HardwareTierLevel | None = None,
) -> ResolvedDispatch:
    """Derive the dispatch lane FROM the already-resolved model's tier.

    The counterpart to :func:`resolve_dispatch`, inverted per the recorded
    settled framework (2026-07-11): tiering is DATA-DECIDED, so the model is
    resolved FIRST — the detector's recommendation with the unpinned->pinned cap,
    :meth:`intergen.model_manager.ModelManager.resolve_for_detected` — and the
    dispatch posture then FOLLOWS the model, never the reverse. A resolved model
    whose tier ships a native logic lane runs NATIVE at that tier; every other
    resolved model runs the LOCKED floor.

    This is what makes the integrated-GPU Tier-2 case correct: the detector
    recommends the 2B for latency, so the resolved model is the 2B (Tier-1) and
    the lane is LOCKED — even though the raw HARDWARE tier is 2. Feeding the raw
    detected tier to :func:`resolve_dispatch` (the old daemon path) instead
    unlocked the native 9B lane for a box that cannot run the 9B, and the daemon
    then dead-ended looking for a 9B onboarding never downloaded.

    An operator override (``dispatch.tier_override``) may only LOWER the lane
    toward the locked floor; it can NEVER raise it above what the resolved model
    was validated in — "no box runs a model in a lane posture it was not validated
    in." Fail-closed: a floor/unknown model tier, or no shipped lane at the
    candidate, resolves to the locked 2B floor.
    """
    candidate = model_tier
    # The override can only TIGHTEN (lower toward the locked floor), never loosen:
    # the model is data-decided; an override cannot conjure a lane the resolved
    # model was not validated in.
    if override_tier is not None and override_tier.value < candidate.value:
        candidate = override_tier

    if candidate != FLOOR_TIER and candidate in shipped_lanes:
        return ResolvedDispatch(
            tier=candidate,
            dispatch_mode=DispatchMode.NATIVE,
            detected_tier=detected_tier,
            override_tier=override_tier,
            fell_back_to_floor=False,
            walked_down=(candidate != model_tier),
        )

    return ResolvedDispatch(
        tier=FLOOR_TIER,
        dispatch_mode=DispatchMode.LOCKED_DOWN,
        detected_tier=detected_tier,
        override_tier=override_tier,
        # A deliberate floor (2B model, or an override-to-floor) is NOT a
        # fail-closed fallback; only a non-floor candidate with no shipped lane is.
        fell_back_to_floor=(candidate != FLOOR_TIER),
    )


# ── System-category conversation (the locked-floor grounding surface) ────────
#
# A turn that is ABOUT this system's administration, privileges, the
# authorization/safety layer, or InterGen's own ability to change system state.
# On the LOCKED 2B floor these must NOT be answered by raw freeform: pressed on
# why a privileged action was gated, the 2B fabricated capability-denial and
# `sudo` folklore ("I can't run commands directly", "run it with sudo", "the
# system is in a privileged mode") — false statements about a capability the
# system HAS. The router grounds such a turn with the true capability facts
# (persona.SYSTEM_CAPABILITY_GUARD) instead of letting the model free-associate.
#
# This is a DISPATCH-POLICY decision (what the locked floor may answer on its
# own), so it lives beside the lockdown resolver. Detection is deliberately broad
# on the privilege/administration/authorization axis — the folklore-risk zone —
# and does not fire on ordinary how-to or knowledge questions.
_SYSTEM_CATEGORY_RE = re.compile(
    r"\b(?:"
    r"privilege|privileges|privileged|unprivileged|"
    r"elevate|elevated|elevation|permission|permissions|"
    r"sudo|pkexec|polkit|superuser|root\s+(?:access|privileges|user)|as\s+root|"
    r"safety\s+layer|state[-\s]?changing|"
    r"administer|administrating|administration|administrator|admin\s+rights|"
    r"(?:update|upgrade|manage|control|configure)\s+(?:the\s+|this\s+)?system"
    r")\b",
    re.IGNORECASE,
)


def is_system_category_conversation(text: str) -> bool:
    """True when the turn is ABOUT system administration, privileges, the
    authorization/safety layer, or InterGen's own ability to change system
    state. On the locked 2B floor the router grounds such a turn in the true
    capability facts rather than letting raw freeform fabricate `sudo` /
    can't-run / 'privileged mode' folklore. Neutral on ordinary how-to and
    knowledge questions (they contain none of these markers)."""
    return bool(_SYSTEM_CATEGORY_RE.search(text or ""))
