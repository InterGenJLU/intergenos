"""Systemd service management — status/start/stop/enable/disable.

Safety tiers:
  auto    — status, is-active, is-enabled, list-units, show
  confirm — start, stop, restart, enable, disable, mask, unmask
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any

from intergen.interfaces.tool import BaseTool
from intergen.interfaces.types import SafetyTier, ToolResult, ToolSchema

log = logging.getLogger(__name__)

AUTO_ACTIONS = frozenset({
    "status", "is-active", "is-enabled", "is-failed",
    "list-units", "list-unit-files", "show", "cat",
    "list-timers", "list-sockets", "list-dependencies",
})

CONFIRM_ACTIONS = frozenset({
    "start", "stop", "restart", "reload", "enable", "disable",
    "mask", "unmask", "daemon-reload",
})


class ManageServicesTool(BaseTool):
    """Manage systemd services via systemctl."""

    @property
    def name(self) -> str:
        return "manage_services"

    @property
    def description(self) -> str:
        return (
            "Manage systemd services. Check status, start, stop, enable, "
            "or disable services. Status queries are automatic; state "
            "changes require user confirmation."
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
                            "status", "is-active", "is-enabled", "list-units",
                            "start", "stop", "restart", "reload",
                            "enable", "disable", "mask", "unmask",
                            "show", "cat", "daemon-reload",
                        ],
                        "description": "The systemctl action to perform.",
                    },
                    "service": {
                        "type": "string",
                        "description": "Service name (e.g., 'sshd', 'NetworkManager'). Required for most actions.",
                    },
                    "user_mode": {
                        "type": "boolean",
                        "description": "Use --user flag for user-level services.",
                        "default": False,
                    },
                },
                "required": ["action"],
            },
            safety_tier=SafetyTier.CONFIRM,
        )

    def classify_safety(self, arguments: dict[str, Any]) -> SafetyTier:
        """Classify based on the action."""
        action = arguments.get("action", "")
        if action in AUTO_ACTIONS:
            return SafetyTier.AUTO
        if action in CONFIRM_ACTIONS:
            return SafetyTier.CONFIRM
        return SafetyTier.CONFIRM

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        """Execute the systemctl action."""
        action = arguments.get("action", "")
        service = arguments.get("service", "")
        user_mode = arguments.get("user_mode", False)

        if not action:
            return ToolResult(
                call_id="", name=self.name,
                content="Error: no action specified", success=False,
            )

        # Most actions require a service name
        if action not in ("list-units", "list-unit-files", "list-timers",
                          "list-sockets", "daemon-reload") and not service:
            return ToolResult(
                call_id="", name=self.name,
                content=f"Error: '{action}' requires a service name",
                success=False,
            )

        cmd = ["systemctl"]
        if user_mode:
            cmd.append("--user")
        cmd.append(action)
        if service:
            cmd.append(service)

        log.info("Service operation: %s %s%s",
                 action, service or "(all)",
                 " (user mode)" if user_mode else "")

        # State-changing actions on system services need sudo
        if action in CONFIRM_ACTIONS and not user_mode:
            cmd = ["sudo"] + cmd

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
            )
            output_parts = []
            if result.stdout:
                output_parts.append(result.stdout.rstrip())
            if result.stderr:
                output_parts.append(f"[stderr] {result.stderr.rstrip()}")

            content = "\n".join(output_parts) if output_parts else "(no output)"

            # systemctl status returns exit code 3 for inactive services —
            # that's informational, not an error
            success = result.returncode == 0
            if action in ("status", "is-active", "is-enabled", "is-failed"):
                success = True  # informational queries always "succeed"

            return ToolResult(
                call_id="", name=self.name,
                content=content,
                success=success,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                call_id="", name=self.name,
                content=f"systemctl {action} timed out after 30 seconds",
                success=False,
            )
        except OSError as e:
            return ToolResult(
                call_id="", name=self.name,
                content=f"Failed to execute systemctl: {e}",
                success=False,
            )
