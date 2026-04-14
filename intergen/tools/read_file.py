"""Read file contents — returns text with line numbers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from intergen.interfaces.tool import BaseTool
from intergen.interfaces.types import SafetyTier, ToolResult, ToolSchema

log = logging.getLogger(__name__)

MAX_FILE_SIZE = 1048576  # 1 MB — refuse files larger than this


class ReadFileTool(BaseTool):
    """Read a file and return its contents."""

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read the contents of a file on the system. "
            "Returns the file text with line numbers. "
            "Supports optional line range (start_line, end_line)."
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
        )
