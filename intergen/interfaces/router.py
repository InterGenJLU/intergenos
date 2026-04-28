"""Conversation router interface — routes user input to handlers."""

from __future__ import annotations
from abc import ABC, abstractmethod

from intergen.interfaces.types import RouteResult


class RouterInterface(ABC):
    """Abstract interface for the conversation router.

    The router receives user input and decides how to handle it:
    1. Regex/keyword match → direct tool call
    2. Semantic embedding match → tool call
    3. LLM tool calling → tool call
    4. LLM free response → text response

    Priority chain (simplified from 18 to 8):
    P1: System commands (shutdown, reboot — safety gated)
    P2: Package management (install, remove, search)
    P3: Service management (start, stop, status)
    P4: File operations (read, write, search)
    P5: Information queries (hardware, disk, network)
    P6: Web search
    P7: Application launch
    P8: LLM free response (fallback)
    """

    @abstractmethod
    def route(self, user_input: str, *,
              conversation_active: bool = False) -> RouteResult:
        """Route user input through the priority chain.

        Args:
            user_input: The user's message text.
            conversation_active: Whether we're in an active conversation.

        Returns:
            RouteResult with response text, source, and metadata.
        """

    @abstractmethod
    def get_status(self) -> dict:
        """Return router status (model, tier, uptime, etc.)."""
