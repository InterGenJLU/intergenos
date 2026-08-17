# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Read file contents — returns text with line numbers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from intergen.interfaces.tool import BaseTool
from intergen.interfaces.types import SafetyTier, ToolResult, ToolSchema

log = logging.getLogger(__name__)

MAX_FILE_SIZE = 1048576  # 1 MB — refuse files larger than this

# G3-22 structured returns: a file read can be up to 1 MB — far past the ~4 KB
# point at which the local 2B times out on synthesis. When the rendered body
# would overflow, hand the MODEL a structural summary (metadata + head + tail)
# and keep the full body for the USER. Config answers sit at the top, log/error
# answers at the bottom, so head+tail beats head-only truncation (the
# pointer-store intuition, arXiv 2511.22729). Below the threshold the model
# gets the full content unchanged (model_summary stays None).
_MODEL_OVERFLOW_CHARS = 4000   # matches continue_after_tool_call's floor
_SUMMARY_HEAD_LINES = 20
_SUMMARY_TAIL_LINES = 10


def summarize_file_read(header: str, body: str) -> str | None:
    """Structural model-facing summary of a large file read, or None when the
    body is small enough to feed the model whole.

    Purely structural/factual — no model-steering (that lives generically in
    LLMRouter._SYNTHESIS_RULES). The head/tail carry UNTRUSTED file bytes; the
    dispatcher scans + spotlights this summary at the trust boundary exactly as
    it does content (intergen-structured-tool-returns-design.md §7), and the
    full body is scanned too, so injection in the omitted middle is still caught.
    """
    if len(header) + 1 + len(body) <= _MODEL_OVERFLOW_CHARS:
        return None
    lines = body.split("\n")
    head = lines[:_SUMMARY_HEAD_LINES]
    tail = lines[-_SUMMARY_TAIL_LINES:] if len(lines) > _SUMMARY_HEAD_LINES else []
    omitted = max(0, len(lines) - len(head) - len(tail))
    parts = [header, "\n".join(head)]
    if omitted:
        parts.append(f"[... {omitted} lines omitted ...]")
    if tail:
        parts.append("\n".join(tail))
    summary = "\n".join(parts)
    # Hard char-cap (handles a few-but-enormous-lines file): stay under the
    # synthesis floor so the summary is not itself re-truncated downstream.
    cap = _MODEL_OVERFLOW_CHARS - 200
    if len(summary) > cap:
        summary = summary[:cap] + "\n[... truncated ...]"
    return summary


class ReadFileTool(BaseTool):
    """Read a file and return its contents."""

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Retrieve the raw CONTENTS of a file as text, with line numbers "
            "(optional start_line/end_line range). Use this when the user wants "
            "to SEE what is in a file. To interpret, explain, or diagnose a "
            "file's contents use analyze_file; to run a shell command use "
            "run_command."
        )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or relative path to the file.",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "First line to read (1-based, default 1).",
                        "default": 1,
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Last line to read (inclusive, default: end of file).",
                    },
                },
                "required": ["path"],
            },
            safety_tier=SafetyTier.AUTO,
        )

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        """Read the file and return contents."""
        path_str = arguments.get("path", "")
        start = arguments.get("start_line", 1)
        end = arguments.get("end_line")

        if not path_str:
            return ToolResult(
                call_id="", name=self.name,
                content="Error: no path provided", success=False,
            )

        path = Path(path_str).expanduser().resolve()

        if not path.exists():
            return ToolResult(
                call_id="", name=self.name,
                content=f"File not found: {path}", success=False,
            )

        if not path.is_file():
            return ToolResult(
                call_id="", name=self.name,
                content=f"Not a regular file: {path}", success=False,
            )

        # Size check
        try:
            size = path.stat().st_size
        except OSError as e:
            return ToolResult(
                call_id="", name=self.name,
                content=f"Cannot stat file: {e}", success=False,
            )

        if size > MAX_FILE_SIZE:
            return ToolResult(
                call_id="", name=self.name,
                content=(
                    f"File too large ({size:,} bytes, max {MAX_FILE_SIZE:,}). "
                    f"Use start_line/end_line to read a section."
                ),
                success=False,
            )

        # Read
        log.info("Reading %s (%d bytes)", path, size)
        try:
            text = path.read_text(errors="replace")
        except OSError as e:
            log.error("Cannot read %s: %s", path, e)
            return ToolResult(
                call_id="", name=self.name,
                content=f"Cannot read file: {e}", success=False,
            )

        lines = text.splitlines()
        total = len(lines)

        # Apply line range
        start = max(1, start)
        if end is None:
            end = total
        end = min(end, total)

        if start > total:
            return ToolResult(
                call_id="", name=self.name,
                content=f"start_line {start} is beyond end of file ({total} lines)",
                success=False,
            )

        selected = lines[start - 1:end]
        numbered = "\n".join(
            f"{i:>6}\t{line}"
            for i, line in enumerate(selected, start=start)
        )

        header = f"File: {path} ({total} lines)"
        if start != 1 or end != total:
            header += f" [showing lines {start}-{end}]"

        return ToolResult(
            call_id="", name=self.name,
            content=f"{header}\n{numbered}",
            success=True,
            model_summary=summarize_file_read(header, numbered),
        )
