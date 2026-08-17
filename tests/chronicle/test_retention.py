#!/usr/bin/env python3
"""Retention: graduated thinning leaves no gaps, pins are exempt from every
rule, and volume-full pruning stops before touching a pin (spec §7, addendum A).
"""

import unittest

from chronicle import retention as _ret

_HOUR = 3600
_DAY = 86400
_WEEK = 7 * _DAY
_MONTH = 30 * _DAY


def _v(vid, seq, wall, pinned=False, size=0):
    return {"version_id": vid, "sequence": seq, "wall_clock": wall,
            "pinned": pinned, "size_bytes": size}


class RetentionTest(unittest.TestCase):
    def test_user_data_thinning_keeps_recent_hourly_and_thins_old(self):
        now = 100 * _DAY
        versions = []
        # 24 hourly in the last day -> all kept (distinct hour buckets).
        for i in range(24):
            versions.append(_v(f"h{i}", i, now - i * _HOUR))
        # Two versions in the SAME old week -> only the newest-by-sequence.
        versions.append(_v("wk-newer", 101, now - 40 * _DAY))
        versions.append(_v("wk-older", 100, now - 40 * _DAY - 3600))
        keep = _ret.thin_keep_user_data(versions, now)
        for i in range(24):
            self.assertIn(f"h{i}", keep, f"hourly h{i} within 24h must be kept")
        self.assertIn("wk-newer", keep)
        self.assertNotIn("wk-older", keep, "older same-week twin is thinned away")

    def test_pin_is_exempt_from_thinning(self):
        now = 100 * _DAY
        # A very old version that thinning would otherwise drop, but pinned.
        versions = [_v("old", 1, now - 300 * _DAY, pinned=True),
                    _v("old2", 2, now - 300 * _DAY - _HOUR)]
        keep = _ret.thin_keep_user_data(versions, now)
        self.assertIn("old", keep, "a pinned version is never thinned")

    def test_prune_set_never_drops_a_pin(self):
        versions = [_v("a", 1, 0, pinned=True), _v("b", 2, 0)]
        # Even if keep_ids omits the pin, prune_set must not return it.
        pruned = _ret.prune_set(versions, keep_ids=set())
        self.assertNotIn("a", pruned)
        self.assertIn("b", pruned)

    def test_restore_points_keep_last_five_plus_pins(self):
        versions = [_v(f"r{i}", i, i, pinned=(i == 0)) for i in range(10)]
        keep = _ret.thin_keep_restore_points(versions, keep_last=5)
        # Newest five by sequence + the pinned oldest.
        for i in range(5, 10):
            self.assertIn(f"r{i}", keep)
        self.assertIn("r0", keep, "pinned oldest restore point survives")

    def test_volume_full_prune_is_oldest_first(self):
        versions = [_v("old", 1, 0, size=100),
                    _v("mid", 2, 0, size=100),
                    _v("new", 3, 0, size=100)]
        order, freed = _ret.volume_full_prune_plan(versions, need_bytes=150)
        self.assertEqual(order[0], "old", "prune oldest first")
        self.assertGreaterEqual(freed, 150)
        self.assertNotIn("new", order[:1])

    def test_volume_full_prune_raises_when_pins_hold_space(self):
        versions = [_v("p1", 1, 0, pinned=True, size=1000),
                    _v("free", 2, 0, size=10)]
        with self.assertRaises(_ret.PinsHoldingSpace):
            _ret.volume_full_prune_plan(versions, need_bytes=500)

    def test_cap_accounting(self):
        self.assertEqual(_ret.cap_room_remaining(1000, 300), 700)
        self.assertTrue(_ret.cap_below_usage(cap_bytes=100, usage_bytes=250))
        self.assertFalse(_ret.cap_below_usage(cap_bytes=300, usage_bytes=250))
        self.assertTrue(_ret.room_ok(700, 500))
        self.assertFalse(_ret.room_ok(400, 500))


if __name__ == "__main__":
    unittest.main()
