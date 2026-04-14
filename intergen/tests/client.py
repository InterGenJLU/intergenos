"""InterGen test client — replaces JARVIS WebSocket client.

Sends messages to InterGen via D-Bus (Ask method) or direct Python
call (for testing without D-Bus daemon running). Returns structured
responses for the assertion engine.

Usage:
    client = InterGenTestClient()
    response = client.ask("What packages are installed?")
    print(response.text, response.source, response.tool_calls)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class TestResponse:
    """Structured response from InterGen for test assertions."""
    text: str
    source: str = ""
    handled: bool = False
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    used_llm: bool = False
    escalated: bool = False
    elapsed_ms: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


class InterGenTestClient:
    """Test client for InterGen — D-Bus or direct mode.

    D-Bus mode: calls com.intergenos.InterGen.Ask() on the session bus.
    Direct mode: instantiates the daemon in-process (no D-Bus required).

    Direct mode is the default for testing since it doesn't require
    the daemon to be running as a systemd service.
    """

    def __init__(self, mode: str = "direct") -> None:
        """Initialize the test client.

        Args:
            mode: "direct" (in-process) or "dbus" (session bus).
        """
        self._mode = mode
        self._daemon = None
        self._dbus_available = False

        if mode == "direct":
            self._init_direct()
        elif mode == "dbus":
            self._init_dbus()
        else:
            raise ValueError(f"Unknown mode: {mode}")

    def ask(self, message: str) -> TestResponse:
        """Send a message to InterGen and return structured response."""
        t0 = time.time()

        if self._mode == "dbus":
            raw = self._ask_dbus(message)
        else:
            raw = self._ask_direct(message)

        elapsed = (time.time() - t0) * 1000  # ms

        return TestResponse(
            text=raw.get("response", raw.get("text", "")),
            source=raw.get("source", ""),
            handled=raw.get("handled", False),
            tool_calls=raw.get("tool_calls", []),
            tool_results=raw.get("tool_results", []),
            used_llm=raw.get("used_llm", False),
            escalated=raw.get("escalated", False),
            elapsed_ms=elapsed,
            raw=raw,
        )

    def status(self) -> dict[str, Any]:
        """Get InterGen daemon status."""
        if self._mode == "dbus":
            return self._status_dbus()
        return self._status_direct()

    def close(self) -> None:
        """Clean up resources."""
        if self._daemon is not None:
            self._daemon.stop_service()
            self._daemon = None

    # --- Direct mode ---

    def _init_direct(self) -> None:
        """Initialize direct (in-process) mode."""
        from intergen.dbus_daemon import InterGenDaemon
        self._daemon = InterGenDaemon()
        self._daemon.start_service()
        log.info("Test client: direct mode initialized")

    def _ask_direct(self, message: str) -> dict[str, Any]:
        """Ask via direct Python call."""
        response = self._daemon.ask(message)
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            return {"response": response, "source": "direct"}

    def _status_direct(self) -> dict[str, Any]:
        """Get status via direct call."""
        return json.loads(self._daemon.status())

    # --- D-Bus mode ---

    def _init_dbus(self) -> None:
        """Initialize D-Bus mode."""
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION)
            self._dbus_available = True
            log.info("Test client: D-Bus mode initialized")
        except Exception as e:
            log.warning("D-Bus not available: %s. Falling back to direct.", e)
            self._mode = "direct"
            self._init_direct()

    def _ask_dbus(self, message: str) -> dict[str, Any]:
        """Ask via D-Bus."""
        from gi.repository import Gio, GLib

        try:
            result = self._bus.call_sync(
                "com.intergenos.InterGen",
                "/com/intergenos/InterGen",
                "com.intergenos.InterGen",
                "Ask",
                GLib.Variant("(s)", (message,)),
                GLib.VariantType("(s)"),
                Gio.DBusCallFlags.NONE,
                120000,  # 120 second timeout for LLM inference
            )
            response_str = result.unpack()[0]
            return json.loads(response_str)
        except Exception as e:
            return {"response": f"D-Bus error: {e}", "source": "error"}

    def _status_dbus(self) -> dict[str, Any]:
        """Get status via D-Bus."""
        from gi.repository import Gio, GLib

        try:
            result = self._bus.call_sync(
                "com.intergenos.InterGen",
                "/com/intergenos/InterGen",
                "com.intergenos.InterGen",
                "Status",
                None,
                GLib.VariantType("(s)"),
                Gio.DBusCallFlags.NONE,
                5000,
            )
            return json.loads(result.unpack()[0])
        except Exception as e:
            return {"error": str(e)}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
