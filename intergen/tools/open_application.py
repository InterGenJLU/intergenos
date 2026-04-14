"""Launch desktop applications via .desktop files or direct command.

Uses GIO (gio launch) for .desktop file launches, falling back to
direct subprocess execution. Discovers installed applications by
scanning standard XDG directories.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from intergen.interfaces.tool import BaseTool
from intergen.interfaces.types import SafetyTier, ToolResult, ToolSchema

log = logging.getLogger(__name__)

# Standard XDG application directories
APP_DIRS = [
    Path("/usr/share/applications"),
    Path("/usr/local/share/applications"),
    Path(os.path.expanduser("~/.local/share/applications")),
]


class OpenApplicationTool(BaseTool):
    """Launch desktop applications."""

    @property
    def name(self) -> str:
        return "open_application"

    @property
    def description(self) -> str:
        return (
            "Launch a desktop application by name. Searches installed "
            ".desktop files to find the application. Can also list all "
            "available applications."
        )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Application name to launch (e.g., 'Firefox', "
                            "'Files', 'Terminal', 'Settings')."
                        ),
                    },
                    "list_apps": {
                        "type": "boolean",
                        "description": "If true, list available applications instead of launching.",
                        "default": False,
                    },
                },
                "required": [],
            },
            safety_tier=SafetyTier.AUTO,
        )

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        """Launch the application or list available apps."""
        if arguments.get("list_apps"):
            return self._list_applications()

        app_name = arguments.get("name", "").strip()
        if not app_name:
            return ToolResult(
                call_id="", name=self.name,
                content="Error: no application name provided",
                success=False,
            )

        return self._launch_application(app_name)

    def _list_applications(self) -> ToolResult:
        """List all installed desktop applications."""
        apps = self._discover_apps()
        if not apps:
            return ToolResult(
                call_id="", name=self.name,
                content="No desktop applications found",
                success=False,
            )

        lines = ["Installed applications:\n"]
        for display_name, desktop_file in sorted(apps, key=lambda x: x[0].lower()):
            lines.append(f"  {display_name}  ({desktop_file.name})")

        return ToolResult(
            call_id="", name=self.name,
            content="\n".join(lines),
            success=True,
        )

    def _launch_application(self, app_name: str) -> ToolResult:
        """Find and launch an application by name."""
        apps = self._discover_apps()
        search = app_name.lower()

        # Exact match first, then substring match
        match = None
        for display_name, desktop_file in apps:
            if display_name.lower() == search:
                match = (display_name, desktop_file)
                break

        if match is None:
            for display_name, desktop_file in apps:
                if search in display_name.lower() or search in desktop_file.stem.lower():
                    match = (display_name, desktop_file)
                    break

        if match is None:
            # Show available apps to help the user
            available = sorted(set(name for name, _ in apps))[:20]
            return ToolResult(
                call_id="", name=self.name,
                content=(
                    f"Application '{app_name}' not found.\n"
                    f"Available: {', '.join(available)}"
                ),
                success=False,
            )

        display_name, desktop_file = match
        log.info("Launching application: %s (%s)", display_name, desktop_file.name)

        # Launch via gio
        try:
            result = subprocess.run(
                ["gio", "launch", str(desktop_file)],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return ToolResult(
                    call_id="", name=self.name,
                    content=f"Launched {display_name}",
                    success=True,
                )
            # gio failed — try gtk-launch
            desktop_id = desktop_file.stem
            result = subprocess.run(
                ["gtk-launch", desktop_id],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return ToolResult(
                    call_id="", name=self.name,
                    content=f"Launched {display_name}",
                    success=True,
                )
            return ToolResult(
                call_id="", name=self.name,
                content=f"Failed to launch {display_name}: {result.stderr}",
                success=False,
            )
        except subprocess.TimeoutExpired:
            # For GUI apps, a timeout on launch is normal (app is running)
            return ToolResult(
                call_id="", name=self.name,
                content=f"Launched {display_name}",
                success=True,
            )
        except OSError as e:
            return ToolResult(
                call_id="", name=self.name,
                content=f"Launch failed: {e}",
                success=False,
            )

    def _discover_apps(self) -> list[tuple[str, Path]]:
        """Discover installed .desktop files and extract display names.

        Returns list of (display_name, desktop_file_path) tuples.
        Skips hidden apps (NoDisplay=true) and apps without a Name.
        """
        apps = []
        seen = set()

        for app_dir in APP_DIRS:
            if not app_dir.is_dir():
                continue
            for desktop_file in app_dir.glob("*.desktop"):
                if desktop_file.name in seen:
                    continue
                seen.add(desktop_file.name)

                name, hidden = self._parse_desktop_file(desktop_file)
                if name and not hidden:
                    apps.append((name, desktop_file))

        return apps

    def _parse_desktop_file(self, path: Path) -> tuple[str | None, bool]:
        """Parse a .desktop file for Name and NoDisplay.

        Returns (name, hidden) tuple.
        """
        name = None
        hidden = False
        in_desktop_entry = False

        try:
            for line in path.read_text(errors="replace").splitlines():
                stripped = line.strip()
                if stripped == "[Desktop Entry]":
                    in_desktop_entry = True
                    continue
                if stripped.startswith("[") and stripped.endswith("]"):
                    if in_desktop_entry:
                        break  # Past the [Desktop Entry] section
                    continue
                if not in_desktop_entry:
                    continue
                if stripped.startswith("Name=") and name is None:
                    name = stripped[5:].strip()
                elif stripped == "NoDisplay=true":
                    hidden = True
                elif stripped == "Hidden=true":
                    hidden = True
        except OSError:
            pass

        return name, hidden
