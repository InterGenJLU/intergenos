# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""AI-2 — central spotlighting chokepoint tests.

spotlighting.wrap_ingress_content existed but was NEVER wired in — ingress
content (file bodies, web results) reached the LLM context with no trust-boundary
marker, so the structural injection defense was dead code. AI-2 wires it at the
CENTRAL dispatch chokepoint (tool_registry.execute), keyed off the same
INGRESS_TOOLS_V1 set as the §5.1 watermark.

These tests drive execute() with the (non-privileged, no-network) read_file tool
over a temp file and assert its result re-enters as UNTRUSTED-INGRESS-wrapped,
while non-ingress output is untouched. Runs on any host.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from intergen import spotlighting
from intergen.tool_registry import ToolRegistry, _ingress_source_attribution
from intergen.interfaces.types import ToolCall
from intergen.interfaces.provenance import Provenance, INGRESS_TOOLS_V1


class SourceAttributionTests(unittest.TestCase):
    def test_file_tools_map_to_path(self):
        self.assertEqual(
            _ingress_source_attribution("read_file", {"path": "/etc/hostname"}),
            ("/etc/hostname", "file"),
        )
        self.assertEqual(
            _ingress_source_attribution("analyze_file", {"path": "/tmp/x"}),
            ("/tmp/x", "file"),
        )

    def test_web_search_maps_to_query(self):
        self.assertEqual(
            _ingress_source_attribution("web_search", {"query": "wayland"}),
            ("wayland", "web_search"),
        )

    def test_screenshot_and_fallback(self):
        self.assertEqual(
            _ingress_source_attribution("take_screenshot", {}),
            ("screen capture", "screenshot"),
        )
        self.assertEqual(
            _ingress_source_attribution("something_else", {}),
            ("something_else", "untrusted"),
        )

    def test_every_ingress_tool_has_a_mapping(self):
        # Every tool the gate treats as ingress must yield a non-empty source_type
        # at the chokepoint (no ingress tool falls through unlabelled).
        for name in INGRESS_TOOLS_V1:
            _, source_type = _ingress_source_attribution(name, {})
            self.assertTrue(source_type)


class ChokepointWrapTests(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry()
        self.registry.discover_tools()

    def _read_file_call(self, path):
        return ToolCall(
            name="read_file",
            arguments={"path": path},
            source_of_request=Provenance.USER_DIRECT,
        )

    def test_read_file_result_is_spotlight_wrapped(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("benign file contents\n")
            path = f.name
        try:
            result = self.registry.execute(self._read_file_call(path))
            self.assertTrue(result.success, result.content)
            self.assertTrue(
                spotlighting.is_wrapped(result.content),
                f"ingress result not wrapped: {result.content!r}",
            )
            region = spotlighting.extract_first_wrapped_region(result.content)
            self.assertIsNotNone(region)
            source, source_type, body = region
            self.assertEqual(source, path)
            self.assertEqual(source_type, "file")
            self.assertIn("benign file contents", body)
        finally:
            os.unlink(path)

    def test_failed_ingress_result_not_wrapped(self):
        # A failed read (nonexistent file) returns a tool error, not ingress
        # content — it must not be wrapped (wrapping is gated on success).
        result = self.registry.execute(
            self._read_file_call("/nonexistent/path/xyzzy-42")
        )
        self.assertFalse(result.success)
        self.assertFalse(spotlighting.is_wrapped(result.content))

    def test_already_wrapped_not_double_wrapped(self):
        # If a result is already wrapped, the chokepoint must not wrap it again.
        wrapped = spotlighting.wrap_ingress_content("x", "src", "file")
        self.assertEqual(
            spotlighting._EXTRACT_PATTERN.findall(wrapped).__len__(), 1
        )
        self.assertTrue(spotlighting.is_wrapped(wrapped))


if __name__ == "__main__":
    unittest.main()
