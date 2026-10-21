# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Tests for take_screenshot (M-003) — the Wayland-native capture path.

Screenshots go through the platform's screenshot portal
(org.freedesktop.portal.Screenshot) in its interactive form: the compositor's
own dialog, where the person chooses what to capture and presses its button.
These pins cover:

  * the dead command-line ladder (grim/import/scrot/gnome-screenshot) is GONE
    — a no-aspirational-fallback-code regression pin;
  * the capture is driven through a portal on a private bus (the real GDBus
    request, subscription, Response and Close; only the dialog is stood in
    for): the request is the interactive dialog, an answer from any peer that
    does not hold the portal's name is not taken, and every failure — no
    portal, a cancelled or otherwise ended dialog, no answer before the
    deadline, a file that is not a PNG or is empty — fails LOUD;
  * an unanswered dialog is CLOSED when the deadline passes, so it cannot take
    a capture after the tool has given up, and the failure says so;
  * execute() surfaces the loud error verbatim and, on success, returns a
    base64 PNG data URI and where the person's screenshot was kept;
  * the shell extension's own capture method, which the portal replaces, is
    gone from the extension, and the tool reaches only the portal;
  * validate_arguments does not pre-judge screenshot availability off a
    discoverable binary (the portal is a runtime service, not a which() hit).
"""

from __future__ import annotations

import base64
import inspect
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from intergen.interfaces.types import SafetyTier
import intergen.tools.take_screenshot as ts
from intergen.tools.take_screenshot import TakeScreenshotTool

try:
    from intergen.tests import screenshot_portal_double as portal

    _PORTAL_UNAVAILABLE = portal.unavailable_reason()
except Exception as exc:  # pragma: no cover - environment-dependent
    _PORTAL_UNAVAILABLE = f"the portal double cannot be imported: {exc}"

# Smallest valid 1x1 PNG, so read-back + base64 exercise real bytes.
_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

_EXTENSION_JS = (Path(__file__).resolve().parents[1]
                 / "panel" / "extension" / "extension.js")


class TestNoDeadLadder(unittest.TestCase):
    """The dead Wayland-non-viable capture ladder must be fully removed."""

    def test_no_dead_capture_binaries_referenced(self):
        # The module docstring legitimately EXPLAINS why grim/scrot/etc. are
        # non-viable (design rationale). The concern is invoked fallback *code*,
        # so strip the docstring and assert the rest is clean.
        import ast

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
        # No which()-gating on screenshot — the portal is proven at capture
        # time, not pre-judged here.
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

    _KEPT = "/home/person/Pictures/Screenshots/Screenshot From 2026.png"

    def setUp(self):
        self.tool = TakeScreenshotTool()

    def test_success_returns_base64_datauri(self):
        with mock.patch.object(
            ts, "_capture_screenshot_via_portal",
            return_value=(_PNG_1x1, self._KEPT),
        ):
            res = self.tool.execute({"source": "screenshot"})
        self.assertTrue(res.success)
        # the picture rides in image_data (the image gate's input at the
        # registry; the model's image part at synthesis), never in content
        self.assertTrue(res.image_data.startswith("data:image/png;base64,"))
        self.assertNotIn("base64,", res.content)
        self.assertIn("the screenshot portal", res.content)
        # the person's own screenshot stays where it was saved, and the result
        # says where that is
        self.assertIn(f"kept at {self._KEPT}", res.content)
        # the payload round-trips to the exact captured bytes
        b64 = res.image_data.split("base64,", 1)[1].strip()
        self.assertEqual(base64.b64decode(b64), _PNG_1x1)

    def test_loud_failure_is_surfaced_verbatim(self):
        with mock.patch.object(
            ts, "_capture_screenshot_via_portal",
            side_effect=RuntimeError("the screenshot portal is unavailable "
                                     "(no such name)"),
        ):
            res = self.tool.execute({"source": "screenshot"})
        self.assertFalse(res.success)
        self.assertIn("Capture failed", res.content)
        self.assertIn("unavailable", res.content)

    def test_never_silent_empty(self):
        # A capture that produced nothing must NOT come back as a green result.
        with mock.patch.object(
            ts, "_capture_screenshot_via_portal",
            side_effect=RuntimeError("produced no image bytes"),
        ):
            res = self.tool.execute({"source": "screenshot"})
        self.assertFalse(res.success)


@unittest.skipIf(_PORTAL_UNAVAILABLE, _PORTAL_UNAVAILABLE or "")
class TestCaptureThroughThePortal(unittest.TestCase):
    """The real capture code against a portal on a private bus."""

    @classmethod
    def setUpClass(cls):
        cls.bus = portal.PrivateBus()
        try:
            cls.portal = portal.PortalDouble(cls.bus)
            cls.portal.start()
            cls.client = cls.bus.connect()
        except BaseException:
            cls.bus.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.client.close_sync(None)
        cls.portal.stop()
        cls.bus.close()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="intergen-shot-case-")
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.shot = self.dir / "Screenshot From 2026-10-08 12-00-00.png"
        self.shot.write_bytes(_PNG_1x1)
        patcher = mock.patch.object(ts, "_session_bus",
                                    return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.portal.reset()

    def _answer(self, **kw):
        self.portal.reset(portal.Answer(**kw))

    def test_the_request_is_the_interactive_dialog(self):
        self._answer(code=0, uri=self.shot.as_uri())
        data, path = ts._capture_screenshot_via_portal()

        self.assertEqual(data, _PNG_1x1)
        self.assertEqual(path, str(self.shot))
        self.assertEqual(len(self.portal.requests), 1)
        request = self.portal.requests[0]
        self.assertIs(
            request["options"].get("interactive"), True,
            "the request must ask for the dialog, where the person chooses "
            "and presses capture — a non-interactive request is a silent grab")
        self.assertTrue(request["options"].get("handle_token"),
                        "the request carries the token its handle is built from")
        self.assertEqual(request["parent_window"], "")
        self.assertEqual(request["sender"], self.client.get_unique_name())
        self.assertTrue(self.shot.exists(),
                        "the person's own screenshot must be left where the "
                        "compositor saved it")

    def test_an_answer_from_a_peer_without_the_portal_name_is_not_taken(self):
        forged = self.dir / "forged.png"
        forged.write_bytes(_PNG_1x1 + b"forged")
        self._answer(code=0, uri=self.shot.as_uri(), forged_uri=forged.as_uri())
        data, path = ts._capture_screenshot_via_portal()

        self.assertEqual(
            (data, path), (_PNG_1x1, str(self.shot)),
            "a Response sent to the request's handle by a peer that does not "
            "hold the portal's name was taken as the capture")

    def test_a_cancelled_dialog_fails_loud(self):
        self._answer(code=1)
        with self.assertRaises(RuntimeError) as ctx:
            ts._capture_screenshot_via_portal()
        self.assertIn("cancelled", str(ctx.exception))

    def test_a_dialog_ended_another_way_fails_loud(self):
        self._answer(code=2)
        with self.assertRaises(RuntimeError) as ctx:
            ts._capture_screenshot_via_portal()
        self.assertIn("ended without a capture", str(ctx.exception))

    def test_an_unanswered_dialog_is_closed_when_the_deadline_passes(self):
        self._answer(silent=True)
        with mock.patch.object(ts, "SCREENSHOT_TIMEOUT_MS", 300):
            with self.assertRaises(RuntimeError) as ctx:
                ts._capture_screenshot_via_portal()
        message = str(ctx.exception)
        self.assertIn("not answered within 0.3 s", message)
        self.assertIn("the request was closed", message)
        self.assertEqual(
            self.portal.closed, [self.portal.requests[0]["handle"]],
            "the request was not closed, so its dialog could still take a "
            "capture after the tool gave up")

    def test_a_close_the_portal_refuses_is_reported(self):
        self._answer(silent=True, close_fails=True)
        with mock.patch.object(ts, "SCREENSHOT_TIMEOUT_MS", 300):
            with self.assertRaises(RuntimeError) as ctx:
                ts._capture_screenshot_via_portal()
        message = str(ctx.exception)
        self.assertIn("closing the request failed", message)
        self.assertIn("may still be on screen", message)
        self.assertEqual(len(self.portal.closed), 1)

    def test_a_file_that_is_not_a_png_is_refused(self):
        not_png = self.dir / "notes.txt"
        not_png.write_text("not an image")
        self._answer(code=0, uri=not_png.as_uri())
        with self.assertRaises(RuntimeError) as ctx:
            ts._capture_screenshot_via_portal()
        self.assertIn("is not a PNG", str(ctx.exception))

    def test_an_empty_capture_fails_loud(self):
        empty = self.dir / "empty.png"
        empty.write_bytes(b"")
        self._answer(code=0, uri=empty.as_uri())
        with self.assertRaises(RuntimeError) as ctx:
            ts._capture_screenshot_via_portal()
        self.assertIn("no image bytes", str(ctx.exception))

    def test_a_success_that_names_no_file_fails_loud(self):
        self._answer(code=0, uri=None)
        with self.assertRaises(RuntimeError) as ctx:
            ts._capture_screenshot_via_portal()
        self.assertIn("named no file", str(ctx.exception))

    def test_no_portal_on_the_bus_fails_loud(self):
        empty_bus = portal.PrivateBus()
        self.addCleanup(empty_bus.close)
        lonely = empty_bus.connect()
        self.addCleanup(lonely.close_sync, None)
        with mock.patch.object(ts, "_session_bus", return_value=lonely):
            with self.assertRaises(RuntimeError) as ctx:
                ts._capture_screenshot_via_portal()
        self.assertIn("the screenshot portal is unavailable",
                      str(ctx.exception))


class TestTheShellCaptureSurfaceIsGone(unittest.TestCase):
    """The extension's own capture method on the session bus is retired whole.

    It answered a full-frame grab to whichever peer passed its checks, and no
    check a same-user peer cannot satisfy was available to it. The portal's
    dialog replaces it, so nothing of it may remain to be called.
    """

    def test_the_extension_exports_nothing_on_the_session_bus(self):
        src = _EXTENSION_JS.read_text()
        for construct in ("register_object", "bus_own_name", "DBusNodeInfo",
                          "com.intergenos.InterGenShell", "Shell.Screenshot",
                          ".screenshot("):
            self.assertNotIn(
                construct, src,
                f"the extension still carries {construct!r} of its own "
                "capture surface")

    def test_the_tool_reaches_only_the_portal(self):
        src = inspect.getsource(ts)
        self.assertNotIn("com.intergenos.InterGenShell", src)
        self.assertEqual(ts.PORTAL_SCREENSHOT_IFACE,
                         "org.freedesktop.portal.Screenshot")


if __name__ == "__main__":
    unittest.main()
