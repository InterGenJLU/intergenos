# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Take Screenshot Tool — M-003.

Captures desktop screenshots or webcam frames as base64-encoded PNGs
for LLM vision analysis.

Screenshots go through the InterGen shell extension's D-Bus surface
(com.intergenos.InterGenShell.Screenshot). On a Wayland session no external
process may grab the screen — the compositor owns every frame — so the only
viable path is a method that runs *inside* the compositor. The InterGen shell
extension provides exactly that, driving the in-process Shell.Screenshot API
and writing the PNG to a path this tool supplies. (The old command-line ladder
— grim/import/scrot/gnome-screenshot — is non-viable on the shipped
GNOME/Wayland stack, and the org.gnome.Shell.Screenshot D-Bus method returns
AccessDenied to out-of-process callers.)

Webcam frames still come from fswebcam/v4l2 — those capture a camera device,
not the display server, so they are unaffected by Wayland.

Pattern: base64 image -> multimodal LLM — the LLM describes every detail in the
captured image.
"""

from __future__ import annotations

import base64
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from intergen.interfaces.tool import BaseTool
from intergen.interfaces.types import SafetyTier, ToolResult, ToolSchema

logger = logging.getLogger(__name__)

# InterGen shell extension's screenshot surface (registered by
# intergen/panel/extension/extension.js while the extension is enabled).
SHELL_BUS_NAME = "com.intergenos.InterGenShell"
SHELL_OBJ_PATH = "/com/intergenos/InterGenShell"
SHELL_IFACE = "com.intergenos.InterGenShell"
# The compositor captures and writes the PNG synchronously; 15s is generous
# headroom over a sub-second grab while still failing rather than hanging.
SCREENSHOT_TIMEOUT_MS = 15000


def _find_webcam_method() -> str | None:
    """Detect an available webcam capture tool, if any."""
    if shutil.which("fswebcam"):
        return "fswebcam"
    if Path("/dev/video0").exists() and shutil.which("ffmpeg"):
        return "ffmpeg"
    return None


def _capture_screenshot_via_shell() -> bytes:
    """Capture the full screen via the InterGen shell extension and return the
    raw PNG bytes.

    Fails LOUD: raises RuntimeError with a real message on any failure (service
    absent, capture error, or an empty image). It never returns empty bytes and
    never hands back a silent zero-byte capture.
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

    # A unique absolute path the compositor writes to; we read it back and
    # remove it. mkstemp creates it 0600-owned by us in the private temp dir.
    fd, tmp_name = tempfile.mkstemp(suffix=".png", prefix="intergen-shot-")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION)
            result = bus.call_sync(
                SHELL_BUS_NAME,
                SHELL_OBJ_PATH,
                SHELL_IFACE,
                "Screenshot",
                GLib.Variant("(sb)", (str(tmp_path), False)),
                GLib.VariantType("(bss)"),
                Gio.DBusCallFlags.NONE,
                SCREENSHOT_TIMEOUT_MS,
            )
        except GLib.Error as e:
            raise RuntimeError(
                "the InterGen screenshot service is unavailable — is the "
                f"InterGen shell extension enabled? ({e.message})"
            ) from e

        success, _filename_used, error = result.unpack()
        if not success:
            raise RuntimeError(
                f"screen capture failed: {error or 'unknown error'}"
            )

        data = tmp_path.read_bytes()
        if not data:
            raise RuntimeError(
                "screen capture reported success but produced no image bytes"
            )
        return data
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


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

    When called without arguments, captures a full desktop screenshot via the
    InterGen shell extension. When called with source="webcam", captures a
    webcam frame instead.
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
        # Screenshot availability is proven at capture time (the shell extension
        # is a runtime service, not a discoverable binary); a real failure there
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
                    return ToolResult(
                        call_id="", name=self.name,
                        content="No webcam capture method available. "
                                "Install fswebcam or connect a webcam.",
                        success=False,
                    )
                image_bytes, method_name = _capture_webcam(method)
            else:
                image_bytes = _capture_screenshot_via_shell()
                method_name = "intergen-shell"

            b64_uri = _encode_for_llm(image_bytes)
            size_kb = len(image_bytes) // 1024

            return ToolResult(
                call_id="", name=self.name,
                content=(
                    f"[Image captured via {method_name}: {size_kb}KB PNG]\n"
                    f"{b64_uri}"
                ),
                success=True,
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
