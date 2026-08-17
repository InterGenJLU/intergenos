"""Tests for the perceived-latency voice filler picker (intergen/voice.py)."""
import random
import unittest

from intergen.voice import FillerPicker, describe_subject, _Pool


class TestFillerPicker(unittest.TestCase):
    def setUp(self):
        random.seed(1234)  # deterministic picks for the no-repeat assertions
        self.picker = FillerPicker()  # loads the in-repo data/voice/fillers.json

    def test_pools_loaded(self):
        self.assertTrue(self.picker.available)
        self.assertGreaterEqual(len(self.picker._hop1), 24)
        self.assertGreaterEqual(len(self.picker._hop2_generic), 20)
        self.assertGreaterEqual(len(self.picker._hop2_templated), 4)

    def test_hop1_no_repeat_within_window(self):
        window = self.picker._window
        picks = [self.picker.hop1() for _ in range(60)]
        for i in range(len(picks)):
            recent = picks[max(0, i - window):i]
            self.assertNotIn(picks[i], recent,
                             f"hop1 repeated within {window}: idx {i}")

    def test_hop2_generic_no_repeat_within_window(self):
        window = self.picker._window
        picks = [self.picker.hop2() for _ in range(60)]
        for i in range(len(picks)):
            recent = picks[max(0, i - window):i]
            self.assertNotIn(picks[i], recent)

    def test_hop2_templated_fills_subject_no_literal_slot(self):
        # A known (tool, action) with a subject phrase uses a templated line.
        seen_templated = False
        for _ in range(20):
            line = self.picker.hop2("manage_packages", "list")
            self.assertNotIn("{what}", line)  # slot always filled
            if "the package list" in line:
                seen_templated = True
        self.assertTrue(seen_templated, "expected a templated line for a known call")

    def test_hop2_unknown_tool_uses_generic(self):
        line = self.picker.hop2("totally_unknown_tool", "frobnicate")
        self.assertNotIn("{what}", line)
        self.assertTrue(line)

    def test_describe_subject_mappings(self):
        self.assertEqual(describe_subject("manage_packages", "list"), "the package list")
        self.assertEqual(describe_subject("analyze_file"), "the analysis")
        self.assertEqual(describe_subject("manage_services", "status", "cups"), "the cups service")
        self.assertEqual(describe_subject("manage_services", "list-units"), "the service list")
        self.assertIsNone(describe_subject("manage_packages", "install"))  # not a read
        self.assertIsNone(describe_subject("nonexistent"))

    def test_missing_asset_disables_gracefully(self):
        p = FillerPicker(path="/nonexistent/fillers.json")
        self.assertFalse(p.available)
        self.assertEqual(p.hop1(), "")
        self.assertEqual(p.hop2(), "")

    def test_pool_window_caps_below_pool_size(self):
        # A window >= pool size must not deadlock — it caps and still returns.
        pool = _Pool(["a", "b", "c"], window=10)
        out = [pool.pick() for _ in range(10)]
        self.assertEqual(len(out), 10)
        self.assertTrue(set(out) <= {"a", "b", "c"})


if __name__ == "__main__":
    unittest.main()
