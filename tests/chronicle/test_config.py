#!/usr/bin/env python3
"""chronicle.conf: off-peak window (daytime, overnight, degenerate), the
size-deferral threshold, exclude globs, and load()'s tolerant parsing."""

import os
import tempfile
import unittest

from chronicle import config as _config


class OffPeakTest(unittest.TestCase):
    def test_daytime_window(self):
        cfg = _config.Config()  # 09:00-18:00 default
        self.assertFalse(cfg.is_off_peak(12 * 60), "noon is working hours")
        self.assertTrue(cfg.is_off_peak(2 * 60), "02:00 is off-peak")
        self.assertTrue(cfg.is_off_peak(22 * 60), "22:00 is off-peak")

    def test_overnight_window(self):
        cfg = _config.Config()
        cfg.work_start = "22:00"
        cfg.work_end = "06:00"
        self.assertFalse(cfg.is_off_peak(23 * 60), "23:00 is working (overnight span)")
        self.assertFalse(cfg.is_off_peak(2 * 60), "02:00 is working (overnight span)")
        self.assertTrue(cfg.is_off_peak(12 * 60), "noon is off-peak here")

    def test_degenerate_window_is_always_off_peak(self):
        cfg = _config.Config()
        cfg.work_start = cfg.work_end = "09:00"
        self.assertTrue(cfg.is_off_peak(9 * 60))
        self.assertTrue(cfg.is_off_peak(0))


class ThresholdTest(unittest.TestCase):
    def test_threshold_is_smaller_of_floor_and_fraction(self):
        cfg = _config.Config()
        cfg.size_floor_bytes = 1_000_000_000
        cfg.free_fraction = 0.05
        # 5% of 2 GiB = ~107 MB < 1 GB floor -> fractional wins.
        free = 2 * 1024 * 1024 * 1024
        self.assertLess(cfg.threshold_bytes(free), cfg.size_floor_bytes)
        # Tiny free space -> fractional ~0 -> floor wins (min ignores 0).
        self.assertEqual(cfg.threshold_bytes(0), cfg.size_floor_bytes)

    def test_exceeds_threshold(self):
        cfg = _config.Config()
        cfg.size_floor_bytes = 1000
        cfg.free_fraction = 0.0
        self.assertTrue(cfg.exceeds_threshold(2000, target_free_bytes=100))
        self.assertFalse(cfg.exceeds_threshold(500, target_free_bytes=100))


class ExcludeTest(unittest.TestCase):
    def test_default_excludes_caches_and_trash(self):
        cfg = _config.Config()
        self.assertTrue(cfg.is_excluded("/home/x/.cache/foo"))
        self.assertTrue(cfg.is_excluded("/home/x/.local/share/Trash/y"))
        self.assertFalse(cfg.is_excluded("/home/x/notes.md"))


class LoadTest(unittest.TestCase):
    def test_absent_file_yields_defaults(self):
        cfg = _config.load("/nonexistent/chronicle.conf")
        self.assertEqual(cfg.work_start, _config.DEFAULT_WORK_START)

    def test_partial_and_malformed_falls_back_per_key(self):
        tmp = tempfile.mkdtemp(prefix="chronicle-conf-")
        p = os.path.join(tmp, "chronicle.conf")
        with open(p, "w") as f:
            f.write("[chronicle]\n"
                    "work_start = 08:00\n"
                    "work_end = not-a-time\n"           # malformed -> default
                    "size_floor_bytes = 500\n"
                    "user_data_paths = /home, /srv/data\n")
        cfg = _config.load(p)
        self.assertEqual(cfg.work_start, "08:00")
        self.assertEqual(cfg.work_end, _config.DEFAULT_WORK_END, "bad time -> default")
        self.assertEqual(cfg.size_floor_bytes, 500)
        self.assertEqual(cfg.user_data_paths, ["/home", "/srv/data"])
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
