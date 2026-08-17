# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""AI-13 — regression guard for the INGRESS_TOOLS_V1 watch-list.

The original watch-list named read_url / read_clipboard / list_directory — none
of which are registered tools — so the §5.1 ingress watermark never tripped on
the real injection vectors and the provenance gate was effectively inert against
cross-tool injection. This test asserts every name in the watch-list resolves to
an actually-registered tool, so the drift cannot silently recur, and that the
known real ingress vectors are covered.
"""

from __future__ import annotations

import unittest

from intergen.interfaces.provenance import INGRESS_TOOLS_V1
from intergen.tool_registry import ToolRegistry


class IngressWatchlistTests(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry()
        self.registry.discover_tools()
        self.registered = set(self.registry._tools.keys())

    def test_every_ingress_tool_is_registered(self):
        # The drift-catcher: a watch-list name that matches no registered tool
        # is dead weight that silently disables the watermark for that vector.
        unknown = INGRESS_TOOLS_V1 - self.registered
        self.assertEqual(
            unknown, set(),
            f"INGRESS_TOOLS_V1 names tools that are not registered: {unknown}. "
            f"Registered tools: {sorted(self.registered)}",
        )

    def test_dead_names_removed(self):
        for dead in ("read_url", "read_clipboard", "list_directory"):
            self.assertNotIn(dead, INGRESS_TOOLS_V1)

    def test_real_ingress_vectors_present(self):
        # The text-bearing ingress producers the gate must watch.
        for vector in ("read_file", "web_search", "analyze_file", "take_screenshot"):
            self.assertIn(vector, INGRESS_TOOLS_V1)
            self.assertIn(vector, self.registered)


if __name__ == "__main__":
    unittest.main()
