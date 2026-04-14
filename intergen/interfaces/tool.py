"""Base tool interface — every InterGen tool implements this."""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any

from intergen.interfaces.types import SafetyTier, ToolSchema, ToolResult


class BaseTool(ABC):
    """Abstract base class for all InterGen tools.

    Each tool lives in intergen/tools/<name>.py and implements this interface.
    The ToolRegistry discovers and loads tools at startup.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique tool identifier (e.g., 'run_command')."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description for LLM system prompt."""

    @property
    @abstractmethod
    def schema(self) -> ToolSchema:
        """OpenAI-compatible function calling schema."""

    @abstractmethod
    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        """Execute the tool with parsed arguments.

        Args:
            arguments: Dict of parameter values matching self.schema.parameters

        Returns:
            ToolResult with content string and success flag.
        """

    def classify_safety(self, arguments: dict[str, Any]) -> SafetyTier:
        """Classify the safety tier for this specific invocation.

        Override this for tools with tiered safety (e.g., run_command).
        Default: returns self.schema.safety_tier.
        """
        return self.schema.safety_tier

    def validate_arguments(self, arguments: dict[str, Any]) -> str | None:
        """Validate arguments before execution.

        Returns:
            None if valid, error message string if invalid.
        """
        required = self.schema.parameters.get("required", [])
        for param in required:
            if param not in arguments:
                return f"Missing required parameter: {param}"
        return None
