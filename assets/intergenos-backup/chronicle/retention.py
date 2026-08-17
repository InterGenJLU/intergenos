# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Graduated retention — thinning that consolidates and never leaves gaps
(spec §7), plus cap-aware room accounting for the directory-class target
(addendum A).

The schedules:
  * User data:    hourly for 24h  → daily for 1 month → weekly while room.
  * Config state: every version for 30 days → daily for a year → weekly.
  * Restore points: the last five, plus any pinned.

Every schedule is pin-exempt: a pinned version is kept by every rule, including
volume-full pruning (spec §7). Volume-full pruning is loud, announced before it
runs, oldest-first, and stops rather than touch a pin — if pins block progress
it raises the honest "target full, pins are holding space" signal instead of
silently violating a pin.

For the directory-class target the "while room" test is measured against the
user-set size CAP, not the volume's free space (addendum A): retention frees
room against the cap, and shrinking the cap below current usage triggers the
same loud announced-pruning path.

Every function here is pure over a version list, so the policy is fully
unit-testable. A version is a dict: {version_id, sequence, wall_clock (epoch
seconds), pinned (bool), size_bytes (optional)}.
"""

_HOUR = 3600
_DAY = 86400
_WEEK = 7 * _DAY
_MONTH = 30 * _DAY
_YEAR = 365 * _DAY


def _newest_per_bucket(versions, bucket_of):
    """Keep the newest (highest sequence) version in each bucket. Returns a set
    of kept version_ids."""
    best = {}
    for v in versions:
        b = bucket_of(v)
        cur = best.get(b)
        if cur is None or v["sequence"] > cur["sequence"]:
            best[b] = v
    return {v["version_id"] for v in best.values()}


def _graduated_keep(versions, now, all_window, daily_window):
    """Shared graduated schedule: keep every version within `all_window`, one
    per day up to `daily_window`, one per week beyond. Pins always kept."""
    keep = set()
    for v in versions:
        if v.get("pinned"):
            keep.add(v["version_id"])
    age = lambda v: now - v["wall_clock"]
    recent = [v for v in versions if age(v) <= all_window]
    daily = [v for v in versions if all_window < age(v) <= daily_window]
    older = [v for v in versions if age(v) > daily_window]
    keep |= {v["version_id"] for v in recent}
    keep |= _newest_per_bucket(daily, lambda v: v["wall_clock"] // _DAY)
    keep |= _newest_per_bucket(older, lambda v: v["wall_clock"] // _WEEK)
    return keep


def thin_keep_user_data(versions, now):
    """User data: hourly ≤24h → daily ≤1 month → weekly."""
    keep = set()
    for v in versions:
        if v.get("pinned"):
            keep.add(v["version_id"])
    age = lambda v: now - v["wall_clock"]
    hourly = [v for v in versions if age(v) <= _DAY]
    daily = [v for v in versions if _DAY < age(v) <= _MONTH]
    weekly = [v for v in versions if age(v) > _MONTH]
    keep |= _newest_per_bucket(hourly, lambda v: v["wall_clock"] // _HOUR)
    keep |= _newest_per_bucket(daily, lambda v: v["wall_clock"] // _DAY)
    keep |= _newest_per_bucket(weekly, lambda v: v["wall_clock"] // _WEEK)
    return keep


def thin_keep_config_state(versions, now):
    """Config state: every version ≤30 days → daily ≤1 year → weekly."""
    return _graduated_keep(versions, now, _MONTH, _YEAR)


def thin_keep_restore_points(versions, keep_last=5):
    """Restore points: the last `keep_last` by sequence, plus every pin."""
    keep = {v["version_id"] for v in versions if v.get("pinned")}
    ordered = sorted(versions, key=lambda v: v["sequence"], reverse=True)
    keep |= {v["version_id"] for v in ordered[:keep_last]}
    return keep


def prune_set(versions, keep_ids):
    """The version_ids to prune = everything not kept. Pins are always in
    keep_ids by construction, but guard here too so no rule can drop a pin."""
    return [
        v["version_id"] for v in versions
        if v["version_id"] not in keep_ids and not v.get("pinned")
    ]


# --------------------------------------------------------------------------
# Volume-full pruning (spec §7) + cap accounting (addendum A)
# --------------------------------------------------------------------------


class PinsHoldingSpace(Exception):
    """Volume-full pruning cannot free enough without touching a pin."""


def volume_full_prune_plan(versions, need_bytes):
    """Plan an oldest-first prune to free at least `need_bytes`, never touching
    a pin.

    Returns (order, freed) where order is the list of version_ids to prune (in
    prune order, oldest first) and freed is the total bytes that frees.

    Raises PinsHoldingSpace if the non-pinned versions cannot free enough — the
    caller surfaces the loud "target full — pinned versions are holding space"
    notice rather than violating a pin.
    """
    prunable = sorted(
        (v for v in versions if not v.get("pinned")),
        key=lambda v: v["sequence"],
    )
    order = []
    freed = 0
    for v in prunable:
        if freed >= need_bytes:
            break
        order.append(v["version_id"])
        freed += int(v.get("size_bytes") or 0)
    if freed < need_bytes:
        raise PinsHoldingSpace(
            f"cannot free {need_bytes} bytes without pruning pinned versions; "
            f"only {freed} bytes are reclaimable. Unpin a version or add "
            f"capacity."
        )
    return order, freed


def cap_room_remaining(cap_bytes, usage_bytes):
    """Room left under a directory-class size cap (addendum A). May be negative
    when usage already exceeds the cap."""
    return int(cap_bytes) - int(usage_bytes)


def cap_below_usage(cap_bytes, usage_bytes):
    """True when the cap is below current usage — the shrink-the-cap case that
    triggers the loud announced-pruning path (addendum A)."""
    return int(usage_bytes) > int(cap_bytes)


def room_ok(free_or_cap_room_bytes, incoming_estimate_bytes):
    """The 'while room remains' test: True when the incoming estimate fits. For
    a whole-volume target pass the volume free space; for a directory-class
    target pass cap_room_remaining(cap, usage) (addendum A)."""
    return incoming_estimate_bytes <= free_or_cap_room_bytes
