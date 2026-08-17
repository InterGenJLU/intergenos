# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Tool call rate limiting — prevents infinite tool loops and salami attacks.

Per B-014: max 5 tool calls per turn, max 2 per-tool per turn.
Tool result compression on chains deeper than 3 calls to prevent
context window exhaustion from accumulated tool output.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from intergen.interfaces.types import ToolCall, ToolResult

logger = logging.getLogger(__name__)

MAX_TOOL_CALLS_PER_TURN = 5
MAX_PER_TOOL_PER_TURN = 2
MAX_TOOL_RESULT_CHARS_DEEP_CHAIN = 500


@dataclass
class ToolLimitTracker:
    """Tracks tool calls within a single conversation turn.

    Created fresh at the start of each routing turn. Used by the web
    server and conversation router to enforce tool call limits.
    """

    total_calls: int = 0
    per_tool: dict[str, int] = field(default_factory=dict)

    def can_call(self, tool_name: str) -> bool:
        """Return True if another call to tool_name is within limits."""
        if self.total_calls >= MAX_TOOL_CALLS_PER_TURN:
            logger.warning(
                "Tool call limit reached: %d calls this turn "
                "(max %d)",
                self.total_calls, MAX_TOOL_CALLS_PER_TURN,
            )
            return False

        count = self.per_tool.get(tool_name, 0)
        if count >= MAX_PER_TOOL_PER_TURN:
            logger.warning(
                "Per-tool limit reached for %s: %d calls (max %d)",
                tool_name, count, MAX_PER_TOOL_PER_TURN,
            )
            return False

        return True

    def record(self, tool_name: str) -> None:
        """Record a tool call."""
        self.total_calls += 1
        self.per_tool[tool_name] = self.per_tool.get(tool_name, 0) + 1


def compress_tool_result(result: ToolResult, call_depth: int) -> ToolResult:
    """Compress tool result content for deep tool call chains.

    After 3 calls in a chain, result content is truncated to prevent
    context window exhaustion from accumulating verbose tool output.
    The full result is still available in the audit log.
    """
    if call_depth <= 3 or not result.content:
        return result

    if len(result.content) > MAX_TOOL_RESULT_CHARS_DEEP_CHAIN:
        compressed = (
            result.content[:MAX_TOOL_RESULT_CHARS_DEEP_CHAIN - 50]
            + f"\n... (truncated at depth {call_depth}, "
            + f"{len(result.content)} chars total)"
        )
        return ToolResult(
            call_id=result.call_id,
            name=result.name,
            content=compressed,
            success=result.success,
        )

    return result


def format_limit_message(tracker: ToolLimitTracker) -> str:
    """Return a human-readable message explaining why limits were hit."""
    if tracker.total_calls >= MAX_TOOL_CALLS_PER_TURN:
        return (
            f"Maximum tool calls per turn ({MAX_TOOL_CALLS_PER_TURN}) "
            f"reached. Additional tool calls are blocked to prevent "
            f"runaway execution."
        )

    for tool_name, count in tracker.per_tool.items():
        if count >= MAX_PER_TOOL_PER_TURN:
            return (
                f"Tool '{tool_name}' has been called {count} times "
                f"this turn (max {MAX_PER_TOOL_PER_TURN}). Further "
                f"calls to this tool are blocked."
            )

    return "Tool call limits are within bounds."