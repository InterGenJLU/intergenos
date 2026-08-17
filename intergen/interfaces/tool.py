# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
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

        Two checks, both structural:

          1. every required parameter is present;
          2. every parameter that declares an ``enum`` in the schema carries
             one of the declared values.

        Check 2 was added 2026-08-12 after a measured defect. A schema declared
        an ``enum`` and nothing enforced it, so a value outside the list — a
        model asking manage_services for action ``list``, which that tool does
        not define — passed validation, reached risk classification, matched
        neither the read-only nor the state-changing action list, and fell
        through to the unrecognised-action default of CONFIRM. On a tool that
        escalates via pkexec that default becomes the privileged tier, so a
        pure read raised an administrator-approval modal for a command
        (``systemctl list``) that systemd would have refused anyway.

        Refusing here is fail-CLOSED: validation runs before the dispatcher
        gate and nothing executes. It is also the honest answer — a value the
        tool does not define is an input error to report, not a risk to ask a
        person to accept. The message names what was rejected and what the tool
        accepts, so the caller can correct it in one hop.

        Returns:
            None if valid, error message string if invalid.
        """
        properties = self.schema.parameters.get("properties", {})
        required = self.schema.parameters.get("required", [])
        for param in required:
            if param not in arguments:
                return f"Missing required parameter: {param}"
        if not isinstance(properties, dict):
            return None
        for param, spec in properties.items():
            if param not in arguments or not isinstance(spec, dict):
                continue
            allowed = spec.get("enum")
            # Only a non-empty list of choices constrains anything; a schema
            # without an enum is unconstrained by design and stays that way.
            if not isinstance(allowed, list) or not allowed:
                continue
            value = arguments[param]
            if value not in allowed:
                return (
                    f"Unsupported {param}: {value!r}. "
                    f"This tool accepts: {', '.join(str(a) for a in allowed)}."
                )
        return None
