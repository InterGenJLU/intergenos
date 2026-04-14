"""Phone a Friend — cloud LLM escalation interface.

InterGen knows his limits. When the local model can't deliver, he escalates
to a cloud provider. The user controls when and how this happens.

LLM-agnostic: the user picks their provider, we provide the means.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator

from intergen.interfaces.types import (
    EscalationMode, LLMResponse, Message, ToolCall, ToolSchema
)


@dataclass
class ProviderConfig:
    """Configuration for a cloud LLM provider."""
    name: str
    adapter: str
    model: str
    api_key_keyring_id: str
    base_url: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.7


@dataclass
class EscalationDecision:
    """Result of deciding whether to escalate to cloud."""
    should_escalate: bool
    reason: str
    confidence: float
    provider: str | None = None


@dataclass
class UsageRecord:
    """Single cloud API call record."""
    provider: str
    model: str
    tokens_prompt: int
    tokens_completion: int
    reason: str
    timestamp: float
    estimated_cost_usd: float = 0.0


class CloudProviderAdapter(ABC):
    """Abstract adapter — one per cloud provider.

    All providers normalize to OpenAI chat completions format internally.
    Pre-built adapters: anthropic, openai, google-genai, mistral,
    deepseek, xai, custom (any OpenAI-compatible endpoint).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name (e.g., 'anthropic', 'openai')."""

    @abstractmethod
    def send(self, messages: list[Message], *,
             tools: list[ToolSchema] | None = None,
             max_tokens: int | None = None,
             temperature: float | None = None) -> LLMResponse:
        """Send a request and get a complete response."""

    @abstractmethod
    def stream(self, messages: list[Message], *,
               tools: list[ToolSchema] | None = None,
               max_tokens: int | None = None,
               temperature: float | None = None) -> Iterator[str | ToolCall]:
        """Stream tokens from the cloud provider."""

    @abstractmethod
    def test_connection(self) -> tuple[bool, str]:
        """Test that the provider is reachable and the API key works.

        Returns:
            (success: bool, message: str)
        """


class EscalationManagerInterface(ABC):
    """Manages the "Phone a Friend" decision and execution.

    Escalation modes (user-configured):
    - never:    Fully offline, no API calls ever
    - fallback: Only when local fails quality gate
    - ask:      InterGen asks "Want me to check with Claude?" (DEFAULT)
    - auto:     InterGen decides based on confidence scoring
    """

    @abstractmethod
    def should_escalate(self, user_message: str, local_response: str,
                        quality_check: str, confidence: float) -> EscalationDecision:
        """Decide whether to escalate to cloud.

        Args:
            user_message: What the user asked.
            local_response: What the local model produced (may be empty).
            quality_check: Result of quality gate (empty string = passed).
            confidence: Local model's self-rated confidence (1-5, <3 triggers).

        Returns:
            EscalationDecision with recommendation and reasoning.
        """

    @abstractmethod
    def escalate(self, messages: list[Message], *,
                 tools: list[ToolSchema] | None = None,
                 reason: str = "") -> LLMResponse:
        """Execute escalation to the configured cloud provider.

        Uses the user's primary provider. Falls back to secondary if configured.
        Logs the call with provider, model, reason, and token count.
        """

    @abstractmethod
    def get_usage(self, last_n_days: int = 30) -> list[UsageRecord]:
        """Return cloud API usage history."""

    @abstractmethod
    def get_mode(self) -> EscalationMode:
        """Return current escalation mode."""

    @abstractmethod
    def set_mode(self, mode: EscalationMode) -> None:
        """Set escalation mode."""

    @abstractmethod
    def configure_provider(self, config: ProviderConfig) -> tuple[bool, str]:
        """Configure a cloud provider. Stores API key in GNOME Keyring.

        Returns:
            (success: bool, message: str)
        """

    @abstractmethod
    def list_providers(self) -> list[ProviderConfig]:
        """List all configured providers (API keys redacted)."""
