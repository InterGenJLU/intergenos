# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Sentinel + phone-a-friend config schema defaults (design plan §5).

Asserts the always-on-by-default scan posture, the NO-default-provider stance,
and dotted access to the new sentinel:/escalation:/providers: keys. The config
file itself is the AI-immutable surface (decision #5); enforcement of that
immutability is the destructive-policy convergence and is tested there.
"""

from __future__ import annotations

import unittest

from intergen.config import Config


class SentinelConfigDefaultTests(unittest.TestCase):
    def setUp(self):
        # No path -> system/user YAML may not exist in the test env; defaults
        # are what we assert. (If a host has a user config it could override,
        # but the keys-exist + structure assertions hold regardless.)
        self.cfg = Config()

    def test_scan_is_always_on_by_default(self):
        self.assertEqual(self.cfg.get("sentinel.scan.mcp"), "always")
        self.assertEqual(self.cfg.get("sentinel.scan.ingress_tools"), "always")

    def test_depth_baseline_default(self):
        self.assertEqual(self.cfg.get("sentinel.scan.depth"), "baseline")
        self.assertEqual(self.cfg.get("sentinel.scan.deep_scanner"), "local-qwen")

    def test_cloud_scanner_opt_in_no_default_provider(self):
        self.assertIs(self.cfg.get("sentinel.cloud_scanner.enabled"), False)
        self.assertIsNone(self.cfg.get("sentinel.cloud_scanner.provider"))

    def test_escalation_defaults_ask_no_default_provider(self):
        self.assertEqual(self.cfg.get("escalation.mode"), "ask")
        self.assertIsNone(self.cfg.get("escalation.primary_provider"))

    def test_providers_empty_by_default(self):
        # Local-only ships ready: no provider configured.
        self.assertEqual(self.cfg.get("providers"), [])


if __name__ == "__main__":
    unittest.main()
