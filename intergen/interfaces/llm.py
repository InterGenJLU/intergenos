"""LLM router interface — local and cloud LLM interaction."""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Iterator

from intergen.interfaces.types import (
    LLMResponse, Message, ToolCall, ToolSchema, EscalationMode
)


class LLMInterface(ABC):
    """Abstract interface for LLM routing (local llama.cpp + cloud fallback)."""

    @abstractmethod
    def chat(self, messages: list[Message], *,
             max_tokens: int | None = None,
             temperature: float | None = None) -> LLMResponse:
        """Generate a complete response.

        Flow: local model → quality gate → retry → cloud fallback (if enabled).

        Args:
            messages: Conversation history in message format.
            max_tokens: Max tokens to generate. Auto-estimated if None.
            temperature: Override default temperature.

        Returns:
            LLMResponse with text, model info, and token counts.
        """

    @abstractmethod
    def stream(self, messages: list[Message], *,
               max_tokens: int | None = None,
               temperature: float | None = None) -> Iterator[str]:
        """Stream tokens from the LLM.

        Yields individual tokens as strings.
        """

    @abstractmethod
    def stream_with_tools(self, messages: list[Message], *,
                          tools: list[ToolSchema],
                          max_tokens: int | None = None,
                          temperature: float | None = None) -> Iterator[str | ToolCall]:
        """Stream tokens with tool calling support.

        Yields:
            str tokens for regular text, or ToolCall when LLM requests a tool.
        """

    @abstractmethod
    def check_quality(self, response: str, user_message: str) -> str:
        """Check if a response meets quality standards.

        Returns:
            Empty string if acceptable, reason string if not
            (e.g., 'empty', 'too_short', 'repetitive', 'echo').
        """

    @abstractmethod
    def get_escalation_mode(self) -> EscalationMode:
        """Return the current cloud escalation mode."""

    @abstractmethod
    def set_escalation_mode(self, mode: EscalationMode) -> None:
        """Set the cloud escalation mode."""
