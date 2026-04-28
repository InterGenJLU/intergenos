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

Skeleton — the conversation router wires into this once router work
lands.

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
        self._router = None
        self._llm = None
        self._tools = None
        self._matcher = None
        self._llama = None
        self._watchdog = None
        self._metrics = None
        self._events = None

    def ask(self, message: str) -> str:
        """Process a user message and return the response."""
        self._requests_handled += 1
        log.info("Ask: %s", message[:100])

        if self._router is None:
            return json.dumps({
                "response": "InterGen is starting up, please wait.",
                "source": "startup",
                "handled": False,
            })

        try:
            result = self._router.route(message)
            return json.dumps({
                "response": result.text,
                "source": result.source,
                "handled": result.handled,
                "tool_calls": [
                    {"name": tc.name, "arguments": tc.arguments}
                    for tc in result.tool_calls
                ],
                "used_llm": result.used_llm,
                "escalated": result.escalated,
            })
        except Exception as e:
            log.error("Ask failed: %s", e)
            self._last_error = str(e)
            if self._metrics:
                self._metrics.record_error(str(e))
            return json.dumps({
                "response": f"I encountered an error: {e}",
                "source": "error",
                "handled": False,
            })

    def status(self) -> str:
        """Return JSON-encoded status."""
        status = {
            "running": self._running,
            "tier": self._hardware_tier,
            "model": self._model_loaded,
            "requests_handled": self._requests_handled,
            "last_error": self._last_error,
            "version": "0.1.0",
            "components": {
                "hardware_detector": self._hardware_tier is not None,
                "model_manager": self._model_loaded is not None,
                "llama_server": self._llama is not None and self._llama.is_running(),
                "router": self._router is not None,
                "semantic_matcher": self._matcher is not None,
                "tools": self._tools is not None,
                "memory": self._memory is not None,
                "watchdog": self._watchdog is not None and self._watchdog.is_running,
            },
        }
        if self._metrics:
            status["metrics"] = self._metrics.get_status()
        if self._router:
            status["router_status"] = self._router.get_status()
        return json.dumps(status, indent=2)

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

        # Load configuration
        from intergen.config import Config
        self._config = Config()

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

        # Step 2: Model manager — check if model is downloaded
        # Environment override: INTERGEN_MODEL_PATH forces a specific model
        # (useful for testing with a different model than tier-recommended)
        import os
        model_path = os.environ.get("INTERGEN_MODEL_PATH")
        if model_path:
            from pathlib import Path
            if Path(model_path).exists():
                self._model_loaded = Path(model_path).stem
                log.info("Model override: %s", model_path)
            else:
                log.error("INTERGEN_MODEL_PATH set but file not found: %s",
                          model_path)
                model_path = None

        if not model_path:
            try:
                from intergen.model_manager import ModelManager
                mm = ModelManager()
                # Use the hardware detector's recommendation (accounts for
                # CPU-only vs GPU-accelerated within the same tier)
                model_info = mm.get_model_by_name(tier.recommended_model)
                if model_info is None:
                    # Fallback to tier-based lookup
                    model_info = mm.get_model_for_tier(tier.tier)
                if model_info and model_info.downloaded:
                    model_path = model_info.local_path
                    self._model_loaded = model_info.name
                    log.info("Model ready: %s at %s", model_info.name, model_path)
                else:
                    log.warning("No model downloaded for Tier %d", tier.tier.value)
            except Exception as e:
                log.warning("Model manager init failed: %s", e)

        # Step 3: Start llama-server
        if model_path:
            try:
                from intergen.llama_manager import LlamaManager
                self._llama = LlamaManager()
                started = self._llama.start(
                    model_path,
                    port=self._config.get("llama_server.port", 8080),
                    context_size=self._config.get("llm.context_size", 16384),
                    gpu_layers=self._config.get("llama_server.gpu_layers", 999),
                    parallel=self._config.get("llama_server.parallel", 1),
                    jinja=self._config.get("llama_server.jinja", True),
                    reasoning=self._config.get("llama_server.reasoning", "off"),
                )
                if started:
                    log.info("llama-server started")
                else:
                    log.warning("llama-server failed to start")
                    self._llama = None
            except Exception as e:
                log.warning("llama-server init failed: %s", e)
                self._llama = None

        # Step 4: Initialize metrics and event logger
        try:
            from intergen.metrics import EventLogger, MetricsTracker
            self._events = EventLogger()
            self._metrics = MetricsTracker()
        except Exception as e:
            log.warning("Metrics init failed: %s", e)

        # Step 5: Initialize tool registry and discover tools
        try:
            from intergen.tool_registry import ToolRegistry
            self._tools = ToolRegistry()
            count = self._tools.discover_tools()
            log.info("Tool registry: %d tools discovered", count)
        except Exception as e:
            log.warning("Tool registry init failed: %s", e)

        # Step 6: Initialize semantic matcher and register intents
        try:
            from intergen.semantic import SemanticMatcher
            self._matcher = SemanticMatcher(
                device=self._config.get("models.embedding_device", "cpu"),
            )
            from intergen.intents import register_all_intents
            register_all_intents(self._matcher)
            log.info("Semantic matcher: %d intents registered",
                     self._matcher.get_intent_count())
        except Exception as e:
            log.warning("Semantic matcher init failed: %s (Layer 2 disabled)", e)
            # Create a matcher without embeddings — keyword matching still works
            from intergen.semantic import SemanticMatcher
            self._matcher = SemanticMatcher.__new__(SemanticMatcher)
            self._matcher._keyword_intents = []
            self._matcher._embedding_intents = {}
            self._matcher._lock = __import__("threading").Lock()
            self._matcher._model = None

        # Step 7: Initialize LLM router
        try:
            from intergen.llm import LLMRouter
            port = self._config.get("llama_server.port", 8080)
            llm_config = {
                "endpoint": f"http://127.0.0.1:{port}/v1/chat/completions",
                "tool_calling": self._llama is not None,
                "temperature": self._config.get("llm.temperature", 0.6),
                "top_p": self._config.get("llm.top_p", 0.8),
                "top_k": self._config.get("llm.top_k", 20),
                "max_tokens": self._config.get("llm.max_tokens", 4096),
                "presence_penalty": self._config.get("llm.presence_penalty", 1.5),
            }
            self._llm = LLMRouter(llm_config)
            log.info("LLM router initialized (tool_calling=%s)",
                     llm_config["tool_calling"])
        except Exception as e:
            log.warning("LLM router init failed: %s", e)

        # Step 8: Initialize memory manager
        self._memory = None
        try:
            from intergen.memory import MemoryManager
            db_path = self._config.get("memory.db_path",
                                        "/var/lib/intergen/data/memory.db")
            self._memory = MemoryManager(db_path)
            log.info("Memory manager initialized (%d facts stored)",
                     self._memory.count)
        except Exception as e:
            log.warning("Memory manager init failed: %s", e)

        # Step 9: Start system state cache
        self._state_cache = None
        try:
            from intergen.state_cache import StateCache
            self._state_cache = StateCache()
            self._state_cache.start()
            log.info("State cache started (%d entries)",
                     self._state_cache.entry_count)
        except Exception as e:
            log.warning("State cache init failed: %s", e)

        # Step 10: Initialize conversation router (the orchestrator)
        if self._tools and self._matcher and self._llm:
            try:
                from intergen.router import ConversationRouter
                from intergen.interfaces.types import HardwareTierLevel
                hw_tier = HardwareTierLevel(
                    self._hardware_tier.get("level", 2)
                ) if self._hardware_tier else HardwareTierLevel.TIER_2
                self._router = ConversationRouter(
                    tool_registry=self._tools,
                    semantic_matcher=self._matcher,
                    llm=self._llm,
                    event_logger=self._events,
                    metrics=self._metrics,
                    hardware_tier=hw_tier,
                    memory=self._memory,
                    state_cache=self._state_cache,
                )
                log.info("Conversation router initialized")
            except Exception as e:
                log.warning("Router init failed: %s", e)
                self._last_error = f"Router init failed: {e}"

        # Step 10: Start watchdog (monitors llama-server health)
        if self._llama:
            try:
                from intergen.watchdog import Watchdog
                self._watchdog = Watchdog(
                    health_check=lambda: self._llama.is_running()
                                         and self._llama.health().running,
                    restart_action=self._llama.restart,
                    on_failure=lambda msg: setattr(self, "_last_error", msg),
                )
                self._watchdog.start()
                log.info("Watchdog started")
            except Exception as e:
                log.warning("Watchdog init failed: %s", e)

        # Step 11: D-Bus export
        self._export_dbus()

        # Step 12: Signal ready
        self._running = True
        log.info("InterGen daemon ready (router=%s, tools=%d, llm=%s)",
                 self._router is not None,
                 self._tools.tool_count if self._tools else 0,
                 self._llm is not None)

    def stop_service(self) -> None:
        """Graceful shutdown — stop all subsystems in reverse order."""
        log.info("InterGen daemon stopping...")
        self._running = False

        if self._state_cache:
            self._state_cache.stop()
        if self._watchdog:
            self._watchdog.stop()
        if self._llama:
            self._llama.stop()

        self._router = None
        self._llm = None
        self._matcher = None
        self._tools = None

        log.info("InterGen daemon stopped")

    def _export_dbus(self) -> None:
        """Export the D-Bus interface via GLib/Gio.

        Uses PyGObject (gi.repository.Gio) — already installed as part
        of the GNOME desktop stack. This is the native GNOME approach,
        no extra pip packages needed.
        """
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib

            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION)
            self._node_info = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)

            def on_method_call(connection, sender, object_path, interface_name,
                               method_name, parameters, invocation):
                """Handle incoming D-Bus method calls."""
                try:
                    if method_name == "Ask":
                        message = parameters.unpack()[0]
                        response = self.ask(message)
                        invocation.return_value(GLib.Variant("(s)", (response,)))
                    elif method_name == "Status":
                        response = self.status()
                        invocation.return_value(GLib.Variant("(s)", (response,)))
                    elif method_name == "GetTier":
                        response = self.get_tier()
                        invocation.return_value(GLib.Variant("(s)", (response,)))
                    else:
                        invocation.return_dbus_error(
                            "com.intergenos.InterGen.Error",
                            f"Unknown method: {method_name}",
                        )
                except Exception as e:
                    log.error("D-Bus method call error: %s", e)
                    invocation.return_dbus_error(
                        "com.intergenos.InterGen.Error", str(e),
                    )

            self._reg_id = self._bus.register_object(
                OBJECT_PATH,
                self._node_info.interfaces[0],
                on_method_call,
                None,  # get_property
                None,  # set_property
            )

            # Own the bus name
            self._owner_id = Gio.bus_own_name_on_connection(
                self._bus,
                SERVICE_NAME,
                Gio.BusNameOwnerFlags.NONE,
                None,  # name_acquired
                None,  # name_lost
            )

            log.info("D-Bus interface exported: %s at %s (via Gio)",
                     SERVICE_NAME, OBJECT_PATH)

        except Exception as e:
            log.warning("D-Bus export failed: %s. Running without D-Bus.", e)
            self._bus = None


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
