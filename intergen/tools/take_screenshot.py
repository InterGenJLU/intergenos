# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Take Screenshot Tool — M-003.

Captures desktop screenshots or webcam frames as base64-encoded PNGs
for LLM vision analysis.

Screenshots go through the platform's screenshot portal
(org.freedesktop.portal.Screenshot), in its interactive form. On a Wayland
session no external process may grab the screen — the compositor owns every
frame — and the portal is the platform's own door to a capture: the request
opens the compositor's screenshot dialog, the person chooses what to capture
and presses its capture button, and the portal answers with the location of
the file the compositor saved. The compositor draws that dialog, so no other
process of the user's can answer it; the click is the consent for each
capture. (The old command-line ladder — grim/import/scrot/gnome-screenshot —
is non-viable on the shipped GNOME/Wayland stack, and the
org.gnome.Shell.Screenshot D-Bus method returns AccessDenied to
out-of-process callers.)

The capture is the person's own screenshot: the compositor saves it where it
saves every screenshot taken from that dialog, and this tool reads it from
there and leaves it in place.

Webcam frames still come from fswebcam/v4l2 — those capture a camera device,
not the display server, so they are unaffected by Wayland.

Pattern: base64 image -> multimodal LLM — the LLM describes every detail in the
captured image.
"""

from __future__ import annotations

import base64
import logging
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from intergen.interfaces.tool import BaseTool
from intergen.interfaces.types import SafetyTier, ToolResult, ToolSchema

logger = logging.getLogger(__name__)

# The platform's screenshot portal (xdg-desktop-portal), as its installed
# interface descriptions name it: org.freedesktop.portal.Screenshot (version 2)
# and the Request object every portal call answers through.
PORTAL_BUS_NAME = "org.freedesktop.portal.Desktop"
PORTAL_OBJ_PATH = "/org/freedesktop/portal/desktop"
PORTAL_SCREENSHOT_IFACE = "org.freedesktop.portal.Screenshot"
PORTAL_REQUEST_IFACE = "org.freedesktop.portal.Request"
PORTAL_REQUEST_PATH_PREFIX = "/org/freedesktop/portal/desktop/request"
# The whole capture, from the request to the file read back. The wait is a
# person choosing what to capture and pressing the dialog's button, so it gets
# the 300 s the assistant's own review card gives a person to decide. On expiry
# the request is closed, so the dialog cannot take a capture after the tool has
# given up, and the tool fails with the reason.
SCREENSHOT_TIMEOUT_MS = 300_000

# Every PNG begins with these eight bytes. The result is handed on labelled
# as a PNG, so the label is checked rather than assumed.
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _find_webcam_method() -> str | None:
    """Detect an available webcam capture tool, if any."""
    if shutil.which("fswebcam"):
        return "fswebcam"
    if Path("/dev/video0").exists() and shutil.which("ffmpeg"):
        return "ffmpeg"
    return None


def _session_bus():
    """The session bus the portal is reached on."""
    from gi.repository import Gio

    return Gio.bus_get_sync(Gio.BusType.SESSION)


def _close_request(bus, handle: str) -> str | None:
    """Close a portal request, ending its dialog. Returns None once the portal
    has accepted the close, or the bus's error message when it has not."""
    from gi.repository import Gio, GLib

    try:
        # -1 is the bus's own default timeout: the close is answered at once.
        bus.call_sync(PORTAL_BUS_NAME, handle, PORTAL_REQUEST_IFACE, "Close",
                      None, None, Gio.DBusCallFlags.NONE, -1, None)
    except GLib.Error as e:
        return e.message
    return None


def _capture_screenshot_via_portal() -> tuple[bytes, str]:
    """Capture a screenshot through the screenshot portal's interactive dialog.

    Returns the PNG bytes and the path of the file the compositor saved. Fails
    LOUD: raises RuntimeError with a real message on every failure (no portal,
    the person cancelled, no answer before the deadline, a file that is not a
    PNG, an empty file). It never returns empty bytes.

    The request is asynchronous: the call returns a Request handle at once and
    the outcome arrives later as that Request's Response signal. The handle is
    known before the call — the portal builds it from this connection's unique
    name and the handle_token sent with the request — so the subscription is in
    place before the call is made and a fast answer cannot be missed. The
    subscription names the portal's bus name as the sender, and GDBus matches
    a well-known sender against that name's current owner, so a Response sent
    to the same handle by any other peer on the session bus is not delivered.
    """
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
    except (ImportError, ValueError) as e:
        raise RuntimeError(
            f"screen capture needs the GObject/Gio bindings, which are "
            f"unavailable: {e}"
        ) from e

    deadline = time.monotonic() + SCREENSHOT_TIMEOUT_MS / 1000

    def remaining_ms() -> int:
        return max(0, int((deadline - time.monotonic()) * 1000))

    try:
        bus = _session_bus()
    except GLib.Error as e:
        raise RuntimeError(
            f"the session bus is unavailable, so the screenshot portal "
            f"cannot be reached ({e.message})"
        ) from e

    token = f"intergen_{secrets.token_hex(8)}"
    sender = (bus.get_unique_name() or "").lstrip(":").replace(".", "_")
    expected_handle = f"{PORTAL_REQUEST_PATH_PREFIX}/{sender}/{token}"

    responses: list[tuple[int, dict]] = []

    def on_response(_conn, _sender, _path, _iface, _signal, params, *_args):
        responses.append(params.unpack())

    def subscribe(handle: str) -> int:
        return bus.signal_subscribe(
            PORTAL_BUS_NAME, PORTAL_REQUEST_IFACE, "Response", handle, None,
            Gio.DBusSignalFlags.NONE, on_response)

    # The Response is dispatched to the context that is the thread's default
    # when the subscription is made, so this call owns one for its duration.
    context = GLib.MainContext.new()
    context.push_thread_default()
    subscription = 0
    try:
        subscription = subscribe(expected_handle)
        options = {
            "handle_token": GLib.Variant("s", token),
            # The dialog, not a silent grab: the person chooses what to
            # capture and presses the button that takes it.
            "interactive": GLib.Variant("b", True),
        }
        try:
            reply = bus.call_sync(
                PORTAL_BUS_NAME, PORTAL_OBJ_PATH, PORTAL_SCREENSHOT_IFACE,
                "Screenshot", GLib.Variant("(sa{sv})", ("", options)),
                GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE,
                remaining_ms(), None)
        except GLib.Error as e:
            raise RuntimeError(
                f"the screenshot portal is unavailable ({e.message})"
            ) from e
        (handle,) = reply.unpack()
        if handle != expected_handle:
            # A portal older than the handle_token convention names the request
            # itself; the interface's own advice is to follow the handle given.
            bus.signal_unsubscribe(subscription)
            subscription = subscribe(handle)

        expired: list[bool] = []

        def on_deadline(*_args) -> bool:
            expired.append(True)
            return False

        timer = GLib.timeout_source_new(remaining_ms())
        timer.set_callback(on_deadline)
        timer.attach(context)
        try:
            while not responses and not expired:
                context.iteration(True)
        finally:
            timer.destroy()

        if not responses:
            close_error = _close_request(bus, handle)
            if close_error is None:
                raise RuntimeError(
                    f"the screenshot dialog was not answered within "
                    f"{SCREENSHOT_TIMEOUT_MS / 1000:g} s, so the request was "
                    f"closed and the dialog can no longer take a capture"
                )
            raise RuntimeError(
                f"the screenshot dialog was not answered within "
                f"{SCREENSHOT_TIMEOUT_MS / 1000:g} s, and closing the request "
                f"failed ({close_error}), so the dialog may still be on screen"
            )
    finally:
        if subscription:
            bus.signal_unsubscribe(subscription)
        # Anything still queued for this call's subscription is released here,
        # on the context it was queued to, before that context is let go.
        while context.iteration(False):
            pass
        context.pop_thread_default()

    code, results = responses[0]
    if code == 1:
        raise RuntimeError(
            "the screenshot was cancelled in the screenshot dialog, so "
            "nothing was captured"
        )
    if code != 0:
        raise RuntimeError(
            f"the screenshot dialog ended without a capture (the portal "
            f"answered {code})"
        )
    uri = results.get("uri") if isinstance(results, dict) else None
    if not isinstance(uri, str) or not uri:
        raise RuntimeError(
            "the screenshot portal reported a capture but named no file"
        )
    path = Gio.File.new_for_uri(uri).get_path()
    if not path:
        raise RuntimeError(
            f"the screenshot portal named {uri}, which is not a local file"
        )
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        raise RuntimeError(
            f"the capture the screenshot portal named could not be read: {e}"
        ) from e
    if not data:
        raise RuntimeError(
            "screen capture reported success but produced no image bytes"
        )
    # The portal's bus name, like every well-known name on the session bus,
    # belongs to whoever holds it, so what it names is checked before it is
    # handed on as a screenshot.
    if not data.startswith(_PNG_SIGNATURE):
        raise RuntimeError(
            f"the file the screenshot portal named ({path}) is not a PNG "
            f"image, so it is not handed on as one"
        )
    return data, path


def _capture_webcam(method: str) -> tuple[bytes, str]:
    """Capture a webcam frame. Returns (png_bytes, tool_name)."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        if method == "fswebcam":
            subprocess.run(
                ["fswebcam", "-q", "--png", "9", "-r", "1280x720",
                 str(tmp_path)],
                check=True, capture_output=True, timeout=10,
            )
        elif method == "ffmpeg":
            subprocess.run(
                ["ffmpeg", "-y", "-f", "v4l2", "-i", "/dev/video0",
                 "-vframes", "1", str(tmp_path)],
                check=True, capture_output=True, timeout=10,
            )
        else:
            raise RuntimeError("No webcam tool available")

        data = tmp_path.read_bytes()
        return data, method
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def _encode_for_llm(image_bytes: bytes) -> str:
    """Encode raw PNG bytes as a base64 data URI for the LLM."""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"


class TakeScreenshotTool(BaseTool):
    """Capture desktop screenshot or webcam frame for vision analysis.

    When called without arguments, captures a screenshot through the screenshot
    portal's dialog, where the person chooses what to capture. When called with
    source="webcam", captures a webcam frame instead.
    """

    def __init__(self) -> None:
        self._webcam = _find_webcam_method()

    @property
    def name(self) -> str:
        return "take_screenshot"

    @property
    def description(self) -> str:
        avail = ["screenshot"]
        if self._webcam:
            avail.append(f"webcam ({self._webcam})")
        return (
            f"Capture an IMAGE from the system: {', '.join(avail)}. "
            "Use this only to capture a picture — not to read files or run "
            "commands. "
            "Provide source='screenshot' or source='webcam'. "
            "The image is returned as base64 data for vision model analysis."
        )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "enum": ["screenshot", "webcam"],
                        "description": "Which image source to capture.",
                    },
                },
                "required": [],
            },
            safety_tier=SafetyTier.CONFIRM,
        )

    def classify_safety(self, arguments: dict[str, Any]) -> SafetyTier:
        return SafetyTier.CONFIRM

    def validate_arguments(self, arguments: dict[str, Any]) -> str | None:
        source = arguments.get("source", "screenshot")
        if source not in ("screenshot", "webcam"):
            return f"Unknown source: {source}. Must be 'screenshot' or 'webcam'."
        # Screenshot availability is proven at capture time (the portal is a
        # runtime service, not a discoverable binary); a real failure there
        # surfaces loudly rather than being pre-judged here.
        if source == "webcam" and self._webcam is None:
            return "No webcam available. Install fswebcam or connect a webcam."
        return None

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        source = arguments.get("source", "screenshot")

        try:
            if source == "webcam":
                method = self._webcam
                if method is None:
                    # Nothing was captured: told in its own words, never
                    # narrated as a capture that errored.
                    return ToolResult(
                        call_id="", name=self.name,
                        content="No webcam capture method available. "
                                "Install fswebcam or connect a webcam.",
                        success=False,
                        executed=False, outcome="unavailable",
                        refusal=("no webcam capture method is available here, "
                                 "so nothing was captured; installing fswebcam "
                                 "or connecting a webcam provides one"),
                    )
                image_bytes, method_name = _capture_webcam(method)
                kept = ""
            else:
                image_bytes, saved_path = _capture_screenshot_via_portal()
                method_name = "the screenshot portal"
                # The file is the person's own screenshot and stays where the
                # compositor saved it; the result says where that is.
                kept = f", kept at {saved_path}"

            b64_uri = _encode_for_llm(image_bytes)
            size_kb = len(image_bytes) // 1024

            # The picture rides in image_data, not in content: the registry
            # puts it through the image gate, and the model receives it as an
            # image part at synthesis. content is the human-readable header.
            return ToolResult(
                call_id="", name=self.name,
                content=f"[Image captured via {method_name}: {size_kb}KB PNG{kept}]",
                success=True,
                image_data=b64_uri,
            )

        except subprocess.CalledProcessError as e:
            return ToolResult(
                call_id="", name=self.name,
                content=f"Capture failed: {e.stderr if e.stderr else str(e)}",
                success=False,
            )
        except Exception as e:
            return ToolResult(
                call_id="", name=self.name,
                content=f"Capture failed: {e}",
                success=False,
            )
