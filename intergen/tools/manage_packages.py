"""Package management via pkm — InterGenOS's native package manager.

pkm may not be installed yet (it's being promoted from build tool to
system tool). This module gracefully handles its absence and provides
clear feedback when pkm isn't available.

Safety tiers:
  auto    — list, search, info, verify
  confirm — install, remove, update
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Any

from intergen.interfaces.tool import BaseTool
from intergen.interfaces.types import SafetyTier, ToolResult, ToolSchema

log = logging.getLogger(__name__)

# Subcommands that are read-only
AUTO_SUBCOMMANDS = frozenset({
    "list", "search", "info", "verify", "query", "status",
    "list-installed", "list-available",
})

# Subcommands that modify the system
CONFIRM_SUBCOMMANDS = frozenset({
    "install", "remove", "uninstall", "update", "upgrade",
})


class ManagePackagesTool(BaseTool):
    """Manage packages via pkm (InterGenOS package manager)."""

    @property
    def name(self) -> str:
        return "manage_packages"

    @property
    def description(self) -> str:
        return (
            "Manage packages on InterGenOS using pkm. "
            "Supports: list, search, info, install, remove, verify. "
            "Install and remove operations require user confirmation."
        )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "list", "search", "info", "install",
                            "remove", "verify", "update",
                        ],
                        "description": "Package operation to perform.",
                    },
                    "package": {
                        "type": "string",
                        "description": "Package name (required for install/remove/info/verify).",
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query (for search action).",
                    },
                },
                "required": ["action"],
            },
            safety_tier=SafetyTier.CONFIRM,
        )

    def classify_safety(self, arguments: dict[str, Any]) -> SafetyTier:
        """Classify based on the action subcommand."""
        action = arguments.get("action", "")
        if action in AUTO_SUBCOMMANDS:
            return SafetyTier.AUTO
        if action in CONFIRM_SUBCOMMANDS:
            return SafetyTier.CONFIRM
        return SafetyTier.CONFIRM

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        """Execute the package management action."""
        action = arguments.get("action", "")
        package = arguments.get("package", "")
        log.info("Package operation: %s %s", action, package or "(all)")
        query = arguments.get("query", "")

        # Check if pkm is available
        pkm_path = shutil.which("pkm")
        if pkm_path is None:
            return ToolResult(
                call_id="", name=self.name,
                content=(
                    "pkm is not installed on this system yet. "
                    "pkm is InterGenOS's native package manager — it needs to "
                    "be promoted from build tool to system tool before package "
                    "management is available."
                ),
                success=False,
            )

        # Build the pkm command
        cmd = self._build_command(action, package, query)
        if cmd is None:
            return ToolResult(
                call_id="", name=self.name,
                content=f"Invalid action or missing parameters for '{action}'",
                success=False,
            )

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120,
            )
            output_parts = []
            if result.stdout:
                output_parts.append(result.stdout)
            if result.stderr:
                output_parts.append(f"[stderr] {result.stderr}")
            if result.returncode != 0:
                output_parts.append(f"[exit code: {result.returncode}]")

            content = "\n".join(output_parts) if output_parts else "(no output)"
            return ToolResult(
                call_id="", name=self.name,
                content=content,
                success=result.returncode == 0,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                call_id="", name=self.name,
                content=f"pkm {action} timed out after 120 seconds",
                success=False,
            )
        except OSError as e:
            return ToolResult(
                call_id="", name=self.name,
                content=f"Failed to execute pkm: {e}",
                success=False,
            )

    def _build_command(self, action: str, package: str, query: str) -> list[str] | None:
        """Build the pkm command list."""
        if action == "list":
            return ["pkm", "list"]
        elif action == "search":
            if not query:
                return None
            return ["pkm", "search", query]
        elif action in ("info", "verify"):
            if not package:
                return None
            return ["pkm", action, package]
        elif action in ("install", "remove", "uninstall"):
            if not package:
                return None
            return ["pkm", action, package]
        elif action in ("update", "upgrade"):
            if package:
                return ["pkm", "update", package]
            return ["pkm", "update"]
        return None
