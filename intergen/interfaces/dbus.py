"""D-Bus daemon interface — system service contract."""

from __future__ import annotations
from abc import ABC, abstractmethod


class InterGenDBusInterface(ABC):
    """D-Bus service interface for com.intergenos.InterGen.

    Exposed methods (callable from GTK4 panel or CLI):
    - Ask(message) -> response
    - Status() -> JSON status string
    - GetTier() -> hardware tier info

    Runs as systemd user service: intergen.service
    """

    @abstractmethod
    def ask(self, message: str) -> str:
        """Process a user message and return the response.

        This is the main entry point. Routes through the conversation
        router, executes tools, streams LLM responses.
        """

    @abstractmethod
    def status(self) -> str:
        """Return JSON-encoded status.

        Includes: tier, model, uptime, escalation_mode, mcp_servers,
        requests_handled, last_error.
        """

    @abstractmethod
    def get_tier(self) -> str:
        """Return hardware tier info as JSON.

        Includes: tier level, RAM, GPU, recommended model, model loaded.
        """

    @abstractmethod
    def start_service(self) -> None:
        """Initialize all subsystems and start serving.

        Startup order:
        1. Detect hardware tier
        2. Download/verify model if needed
        3. Start llama-server
        4. Initialize semantic matcher
        5. Register tools
        6. Connect MCP servers
        7. Export D-Bus interface
        8. Signal ready
        """

    @abstractmethod
    def stop_service(self) -> None:
        """Graceful shutdown: stop llama-server, disconnect MCP, unexport D-Bus."""
