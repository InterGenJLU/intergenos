"""Write or edit files — generates diff for user confirmation."""

from __future__ import annotations

import difflib
import logging
from pathlib import Path
from typing import Any

from intergen.interfaces.tool import BaseTool
from intergen.interfaces.types import SafetyTier, ToolResult, ToolSchema

log = logging.getLogger(__name__)

# Paths that should never be written to
PROTECTED_PATHS = frozenset({
    "/etc/passwd", "/etc/shadow", "/etc/group", "/etc/gshadow",
    "/etc/sudoers", "/boot/vmlinuz", "/boot/initramfs",
})

# Path prefixes that require extra caution
SENSITIVE_PREFIXES = (
    "/etc/", "/boot/", "/usr/lib/systemd/", "/usr/lib/modules/",
)


class WriteFileTool(BaseTool):
    """Write or edit a file, showing a diff before confirming."""

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return (
            "Write content to a file. For existing files, generates a "
            "unified diff so the user can review changes before confirming. "
            "Can create new files or overwrite existing ones."
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
                        "description": "Absolute or relative path for the file.",
                    },
                    "content": {
                        "type": "string",
                        "description": "The full content to write to the file.",
                    },
                    "create_dirs": {
                        "type": "boolean",
                        "description": "Create parent directories if they don't exist.",
                        "default": False,
                    },
                },
                "required": ["path", "content"],
            },
            safety_tier=SafetyTier.CONFIRM,
        )

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        """Write the file and return a diff."""
        path_str = arguments.get("path", "")
        content = arguments.get("content", "")
        create_dirs = arguments.get("create_dirs", False)

        if not path_str:
            return ToolResult(
                call_id="", name=self.name,
                content="Error: no path provided", success=False,
            )

        path = Path(path_str).expanduser().resolve()

        # Check protected paths
        if str(path) in PROTECTED_PATHS:
            return ToolResult(
                call_id="", name=self.name,
                content=f"Blocked: {path} is a protected system file",
                success=False,
            )

        # Generate diff for existing files
        diff_text = ""
        if path.exists():
            try:
                old_content = path.read_text(errors="replace")
                if old_content == content:
                    return ToolResult(
                        call_id="", name=self.name,
                        content=f"No changes needed — file already has this content: {path}",
                        success=True,
                    )
                diff_lines = difflib.unified_diff(
                    old_content.splitlines(keepends=True),
                    content.splitlines(keepends=True),
                    fromfile=f"a/{path.name}",
                    tofile=f"b/{path.name}",
                )
                diff_text = "".join(diff_lines)
            except OSError as e:
                return ToolResult(
                    call_id="", name=self.name,
                    content=f"Cannot read existing file for diff: {e}",
                    success=False,
                )

        # Create parent dirs if requested
        if create_dirs:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                return ToolResult(
                    call_id="", name=self.name,
                    content=f"Cannot create directories: {e}",
                    success=False,
                )

        # Write the file
        try:
            path.write_text(content)
        except OSError as e:
            return ToolResult(
                call_id="", name=self.name,
                content=f"Write failed: {e}",
                success=False,
            )

        if diff_text:
            result_text = f"Updated {path}:\n{diff_text}"
        else:
            line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
            result_text = f"Created {path} ({line_count} lines)"

        log.info("Wrote %s", path)
        return ToolResult(
            call_id="", name=self.name,
            content=result_text,
            success=True,
        )

    def classify_safety(self, arguments: dict[str, Any]) -> SafetyTier:
        """All writes require confirmation. Protected paths are blocked."""
        path_str = arguments.get("path", "")
        if not path_str:
            return SafetyTier.BLOCKED

        path = Path(path_str).expanduser().resolve()
        if str(path) in PROTECTED_PATHS:
            return SafetyTier.BLOCKED

        return SafetyTier.CONFIRM
