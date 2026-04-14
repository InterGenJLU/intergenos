"""D-Bus daemon skeleton — com.intergenos.InterGen service.

Exposes InterGen over D-Bus so the GNOME panel applet, CLI tools,
and other desktop applications can communicate with the AI assistant.

Service name: com.intergenos.InterGen
Interface:    com.intergenos.InterGen
Object path:  /com/intergenos/InterGen

Methods:
  Ask(message: str) -> str
  Status() -> str (JSON)
  GetTier() -> str (JSON)

This is the skeleton — claude-main will wire it to the conversation
router once both branches are merged.

Runs as: systemd user service (intergen.service)
Requires: dbus-python (or pydbus/dasbus)
"""

from __future__ import annotations

import json
import logging
import signal
import sys
from typing import Any

from intergen.interfaces.dbus import InterGenDBusInterface
from intergen.interfaces.types import HardwareTierLevel

log = logging.getLogger(__name__)

SERVICE_NAME = "com.intergenos.InterGen"
OBJECT_PATH = "/com/intergenos/InterGen"
INTERFACE_NAME = "com.intergenos.InterGen"

# D-Bus introspection XML for the interface
INTROSPECTION_XML = f"""
<node>
  <interface name="{INTERFACE_NAME}">
    <method name="Ask">
      <arg direction="in" name="message" type="s"/>
      <arg direction="out" name="response" type="s"/>
    </method>
    <method name="Status">
      <arg direction="out" name="status" type="s"/>
    </method>
    <method name="GetTier">
      <arg direction="out" name="tier" type="s"/>
    </method>
  </interface>
</node>
"""


class InterGenDaemon(InterGenDBusInterface):
    """D-Bus service skeleton for InterGen.

    The daemon initializes subsystems in order:
      1. Hardware detection → tier assignment
      2. Model download/verification
      3. llama-server startup
      4. (future: semantic matcher, tool registry, MCP)
      5. D-Bus interface export

    Currently a skeleton — subsystem wiring happens after merge.
    """

    def __init__(self) -> None:
        self._running = False
        self._hardware_tier: dict[str, Any] | None = None
        self._model_loaded: str | None = None
        self._requests_handled = 0
        self._last_error: str | None = None

    def ask(self, message: str) -> str:
        """Process a user message and return the response.

        Skeleton: returns a placeholder until the router is wired in.
        """
        self._requests_handled += 1
        log.info("Ask: %s", message[:100])

        # Skeleton response — the router will replace this
        return json.dumps({
            "response": (
                "InterGen is running but the conversation router isn't "
                "connected yet. Hardware detected, D-Bus interface active."
            ),
            "source": "dbus-skeleton",
            "handled": False,
        })

    def status(self) -> str:
        """Return JSON-encoded status."""
        return json.dumps({
            "running": self._running,
            "tier": self._hardware_tier,
            "model": self._model_loaded,
            "requests_handled": self._requests_handled,
            "last_error": self._last_error,
            "version": "0.1.0",
            "components": {
                "hardware_detector": True,
                "model_manager": True,
                "llama_server": False,  # skeleton — not started yet
                "router": False,        # owned by claude-main
                "semantic_matcher": False,
                "mcp_client": False,
                "tools": True,
            },
        }, indent=2)

    def get_tier(self) -> str:
        """Return hardware tier info as JSON."""
        if self._hardware_tier is None:
            return json.dumps({"error": "Hardware not detected yet"})
        return json.dumps(self._hardware_tier, indent=2)

    def start_service(self) -> None:
        """Initialize subsystems and start serving.

        Startup order:
          1. Detect hardware tier
          2. Download/verify model if needed
          3. Start llama-server
          4. Initialize semantic matcher (future)
          5. Register tools (future)
          6. Connect MCP servers (future)
          7. Export D-Bus interface
          8. Signal ready
        """
        log.info("InterGen daemon starting...")

        # Step 1: Hardware detection
        try:
            from intergen.hardware import HardwareDetector
            detector = HardwareDetector()
            tier = detector.detect()
            self._hardware_tier = {
                "level": tier.tier.value,
                "ram_gb": tier.ram_gb,
                "gpu_vendor": tier.gpu_vendor,
                "gpu_model": tier.gpu_model,
                "recommended_model": tier.recommended_model,
                "recommended_quant": tier.recommended_quant,
                "estimated_model_size_gb": tier.estimated_model_size_gb,
            }
            log.info("Hardware: Tier %d (%.1f GB RAM, %s)",
                     tier.tier.value, tier.ram_gb, tier.gpu_vendor or "no GPU")
        except Exception as e:
            self._last_error = f"Hardware detection failed: {e}"
            log.error(self._last_error)

        # Step 2-6: Placeholder — subsystems will be wired after merge
        # The model manager, llama server, router, etc. will be initialized
        # here once claude-main's modules are available.

        # Step 7: D-Bus export
        self._export_dbus()

        # Step 8: Signal ready
        self._running = True
        log.info("InterGen daemon ready")

    def stop_service(self) -> None:
        """Graceful shutdown."""
        log.info("InterGen daemon stopping...")
        self._running = False
        # Future: stop llama-server, disconnect MCP, etc.
        log.info("InterGen daemon stopped")

    def _export_dbus(self) -> None:
        """Export the D-Bus interface.

        Tries dasbus first (modern, pure Python), falls back to
        dbus-python if available. If neither is installed, logs a
        warning and runs without D-Bus.
        """
        if self._try_dasbus():
            return
        if self._try_dbus_python():
            return
        log.warning(
            "No D-Bus library available (tried dasbus, dbus-python). "
            "InterGen will run without D-Bus integration. "
            "Install dasbus: pip install dasbus"
        )

    def _try_dasbus(self) -> bool:
        """Try to export via dasbus."""
        try:
            from dasbus.connection import SessionMessageBus
            from dasbus.server.interface import dbus_interface
            from dasbus.typing import Str
            log.info("Using dasbus for D-Bus integration")
            # Actual registration would happen here
            # For the skeleton, we just confirm the library is available
            return True
        except ImportError:
            return False

    def _try_dbus_python(self) -> bool:
        """Try to export via dbus-python."""
        try:
            import dbus
            import dbus.service
            log.info("Using dbus-python for D-Bus integration")
            return True
        except ImportError:
            return False


def main() -> None:
    """Entry point for the InterGen daemon."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    daemon = InterGenDaemon()

    # Handle signals for clean shutdown
    def shutdown_handler(signum: int, frame: Any) -> None:
        log.info("Received signal %d, shutting down", signum)
        daemon.stop_service()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    daemon.start_service()

    # In production, the D-Bus main loop would run here.
    # For the skeleton, we just confirm everything initialized.
    log.info("InterGen daemon initialized. D-Bus service: %s", SERVICE_NAME)
    log.info("Status: %s", daemon.status())


if __name__ == "__main__":
    main()
