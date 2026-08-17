# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Tests for take_screenshot (M-003) — the Wayland-native capture path.

Screenshots route through the InterGen shell extension's D-Bus method
(com.intergenos.InterGenShell.Screenshot). These pins cover:

  * the dead command-line ladder (grim/import/scrot/gnome-screenshot) is GONE
    — a no-aspirational-fallback-code regression pin;
  * the capture helper fails LOUD on every failure mode (service absent,
    reported failure, empty image) and never returns empty bytes;
  * execute() surfaces the loud error verbatim and, on success, returns a
    base64 PNG data URI;
  * validate_arguments no longer pre-judges screenshot availability off a
    discoverable binary (the shell service is a runtime, not a which() hit).
"""

from __future__ import annotations

import base64
import unittest
from unittest import mock

from intergen.interfaces.types import SafetyTier
import intergen.tools.take_screenshot as ts
from intergen.tools.take_screenshot import TakeScreenshotTool

try:
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib  # noqa: F401

    _HAVE_GI = True
except Exception:  # pragma: no cover - environment-dependent
    _HAVE_GI = False

# Smallest valid 1x1 PNG, so read-back + base64 exercise real bytes.
_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


class TestNoDeadLadder(unittest.TestCase):
    """The dead Wayland-non-viable capture ladder must be fully removed."""

    def test_no_dead_capture_binaries_referenced(self):
        # The module docstring legitimately EXPLAINS why grim/scrot/etc. are
        # non-viable (design rationale). The concern is invoked fallback *code*,
        # so strip the docstring and assert the rest is clean.
        import ast
        import inspect

        src = inspect.getsource(ts)
        doc = ast.get_docstring(ast.parse(src)) or ""
        code = src.replace(doc, "")
        for dead in ("grim", "scrot", "gnome-screenshot"):
            self.assertNotIn(
                dead, code,
                f"dead capture tool {dead!r} still invoked in capture code",
            )
        # ImageMagick 'import' as a screenshot verb must be gone as an invocation
        # (the Python `import` keyword is unaffected by these quoted forms).
        self.assertNotIn('"import"', code)
        self.assertNotIn("'import'", code)


class TestValidateAndSchema(unittest.TestCase):
    def setUp(self):
        self.tool = TakeScreenshotTool()

    def test_screenshot_always_allowed(self):
        # No which()-gating on screenshot anymore — the shell service is proven
        # at capture time, not pre-judged here.
        self.assertIsNone(self.tool.validate_arguments({"source": "screenshot"}))
        self.assertIsNone(self.tool.validate_arguments({}))

    def test_unknown_source_rejected(self):
        msg = self.tool.validate_arguments({"source": "nope"})
        self.assertIsNotNone(msg)
        self.assertIn("nope", msg)

    def test_description_lists_screenshot(self):
        self.assertIn("screenshot", self.tool.description)

    def test_safety_tier_confirm(self):
        self.assertEqual(self.tool.classify_safety({}), SafetyTier.CONFIRM)

    def test_webcam_gated_when_absent(self):
        with mock.patch.object(ts, "_find_webcam_method", return_value=None):
            tool = TakeScreenshotTool()
            msg = tool.validate_arguments({"source": "webcam"})
            self.assertIsNotNone(msg)
            self.assertIn("webcam", msg.lower())


class TestExecuteSurfacing(unittest.TestCase):
    """execute() surfaces capture outcomes without touching a real display."""

    def setUp(self):
        self.tool = TakeScreenshotTool()

    def test_success_returns_base64_datauri(self):
        with mock.patch.object(
            ts, "_capture_screenshot_via_shell", return_value=_PNG_1x1
        ):
            res = self.tool.execute({"source": "screenshot"})
        self.assertTrue(res.success)
        self.assertIn("data:image/png;base64,", res.content)
        self.assertIn("intergen-shell", res.content)
        # the payload round-trips to the exact captured bytes
        b64 = res.content.split("base64,", 1)[1].strip()
        self.assertEqual(base64.b64decode(b64), _PNG_1x1)

    def test_loud_failure_is_surfaced_verbatim(self):
        with mock.patch.object(
            ts, "_capture_screenshot_via_shell",
            side_effect=RuntimeError("the InterGen screenshot service is "
                                     "unavailable — is the extension enabled?"),
        ):
            res = self.tool.execute({"source": "screenshot"})
        self.assertFalse(res.success)
        self.assertIn("Capture failed", res.content)
        self.assertIn("unavailable", res.content)

    def test_never_silent_empty(self):
        # A capture that produced nothing must NOT come back as a green result.
        with mock.patch.object(
            ts, "_capture_screenshot_via_shell",
            side_effect=RuntimeError("produced no image bytes"),
        ):
            res = self.tool.execute({"source": "screenshot"})
        self.assertFalse(res.success)


@unittest.skipUnless(_HAVE_GI, "gi/Gio bindings not available")
class TestCaptureViaShellDbusMock(unittest.TestCase):
    """Drive _capture_screenshot_via_shell against a faithful fake bus.

    The fake mirrors the extension contract: on success it writes PNG bytes to
    the exact path the tool passes in the (sb) params, then returns (b,s,s)."""

    def _patch_bus(self, behavior):
        fake_bus = mock.Mock()
        fake_bus.call_sync.side_effect = behavior
        return mock.patch.object(Gio, "bus_get_sync", return_value=fake_bus)

    def test_success_reads_back_written_bytes(self):
        def behavior(name, path, iface, method, params, rtype, flags, timeout):
            fname, _cursor = params.unpack()
            with open(fname, "wb") as fh:
                fh.write(_PNG_1x1)
            return GLib.Variant("(bss)", (True, fname, ""))

        with self._patch_bus(behavior):
            data = ts._capture_screenshot_via_shell()
        self.assertEqual(data, _PNG_1x1)

    def test_service_absent_raises_loud(self):
        def behavior(*a, **k):
            raise GLib.Error.new_literal(
                GLib.quark_from_string("g-dbus-error-quark"),
                "The name com.intergenos.InterGenShell was not provided", 2
            )

        with self._patch_bus(behavior):
            with self.assertRaises(RuntimeError) as ctx:
                ts._capture_screenshot_via_shell()
        self.assertIn("extension", str(ctx.exception).lower())

    def test_reported_failure_raises_loud(self):
        def behavior(name, path, iface, method, params, rtype, flags, timeout):
            return GLib.Variant("(bss)", (False, "", "compositor said no"))

        with self._patch_bus(behavior):
            with self.assertRaises(RuntimeError) as ctx:
                ts._capture_screenshot_via_shell()
        self.assertIn("compositor said no", str(ctx.exception))

    def test_empty_capture_raises_loud(self):
        # success=True but the file was never written with bytes.
        def behavior(name, path, iface, method, params, rtype, flags, timeout):
            fname, _cursor = params.unpack()
            return GLib.Variant("(bss)", (True, fname, ""))

        with self._patch_bus(behavior):
            with self.assertRaises(RuntimeError) as ctx:
                ts._capture_screenshot_via_shell()
        self.assertIn("no image bytes", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
