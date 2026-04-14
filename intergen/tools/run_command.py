"""Shell command execution with tiered safety classification.

Safety tiers:
  auto    — read-only commands (ls, cat, grep, df, ps, uname, etc.)
  confirm — write commands (mkdir, cp, mv, chmod, chown, etc.)
  blocked — destructive commands (rm -rf /, mkfs, dd if=/dev/zero, etc.)

When uncertain, defaults to 'confirm'. The classifier errs on the side
of caution — a write command misclassified as 'auto' is dangerous,
but a read command misclassified as 'confirm' is merely annoying.
"""

from __future__ import annotations

import logging
import re
import subprocess
from typing import Any

from intergen.interfaces.tool import BaseTool
from intergen.interfaces.types import SafetyTier, ToolResult, ToolSchema

log = logging.getLogger(__name__)

# Commands that are always safe (read-only)
AUTO_COMMANDS = frozenset({
    "arch", "cal", "cat", "date", "df", "dig", "du", "echo", "env",
    "file", "find", "free", "getent", "grep", "head", "hostname",
    "id", "ip", "journalctl", "last", "less", "locale", "ls",
    "lsblk", "lscpu", "lsmod", "lsof", "lspci", "lsusb", "man",
    "more", "mount", "nproc", "pgrep", "ping", "printenv", "ps",
    "pwd", "readlink", "rg", "route", "sensors", "ss", "stat",
    "systemctl", "tail", "test", "time", "top", "traceroute",
    "uname", "uptime", "w", "wc", "which", "who", "whoami",
})

# systemctl subcommands that are read-only
SYSTEMCTL_AUTO_SUBS = frozenset({
    "status", "is-active", "is-enabled", "is-failed", "list-units",
    "list-unit-files", "show", "cat", "list-timers", "list-sockets",
    "list-dependencies",
})

# Commands that should always be blocked
BLOCKED_COMMANDS = frozenset({
    "mkfs", "fdisk", "gdisk", "parted", "wipefs", "sgdisk",
    "shred", "shutdown", "reboot", "poweroff", "halt", "init",
})

# Patterns that indicate destructive intent
BLOCKED_PATTERNS = [
    re.compile(r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?/\s*$"),  # rm -rf /
    re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f"),              # rm -rf (any path)
    re.compile(r"\bdd\s+.*if=/dev/(zero|urandom|random)"),     # dd wipe
    re.compile(r"\bdd\s+.*of=/dev/[a-z]"),                     # dd write to any device
    re.compile(r"\b:\s*\(\)\s*\{\s*:\s*\|\s*:"),               # fork bomb
    re.compile(r"\bmkfs\b"),                                    # filesystem format
    re.compile(r">\s*/dev/sd[a-z]"),                            # redirect to disk
    re.compile(r">\s*/dev/nvme"),                               # redirect to NVMe
    re.compile(r"\bchmod\s+.*-R\s+777\s+/\s*$"),               # chmod 777 /
    re.compile(r"\bchown\s+.*-R\s+.*\s+/\s*$"),                # chown -R ... /
    re.compile(r"\bswapon\s+/dev/"),                            # swapon device
    re.compile(r"\bmount\s+.*-o\s+.*remount.*\s+/\s*$"),       # remount root
    re.compile(r"\biptables\s+-F"),                             # flush firewall
    re.compile(r"\bnftables\s+flush\s+ruleset"),                # flush nftables
    re.compile(r"\bsystemctl\s+(mask|disable)\s+(NetworkManager|dbus|systemd)"),  # disable critical services
]

# Commands that modify state (need confirmation)
CONFIRM_COMMANDS = frozenset({
    "apt", "chmod", "chown", "cp", "curl", "dnf", "git",
    "install", "kill", "killall", "ln", "make", "mkdir", "mktemp",
    "mv", "pacman", "patch", "pip", "pkm", "rm", "rmdir", "rsync",
    "sed", "service", "snap", "sort", "sudo", "systemctl", "tar",
    "tee", "touch", "useradd", "userdel", "usermod", "wget",
    "xargs", "yum", "zypper",
})

MAX_OUTPUT_BYTES = 65536  # 64 KB — truncate beyond this


class RunCommandTool(BaseTool):
    """Execute shell commands with tiered safety classification."""

    @property
    def name(self) -> str:
        return "run_command"

    @property
    def description(self) -> str:
        return (
            "Execute a shell command on the InterGenOS system. "
            "Read-only commands run automatically. Write commands require "
            "user confirmation. Destructive commands are blocked."
        )

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default 30, max 300).",
                        "default": 30,
                    },
                },
                "required": ["command"],
            },
            safety_tier=SafetyTier.CONFIRM,  # default; overridden by classify_safety
        )

    def classify_safety(self, arguments: dict[str, Any]) -> SafetyTier:
        """Classify command safety tier."""
        command = arguments.get("command", "").strip()
        if not command:
            return SafetyTier.BLOCKED

        # Check blocked patterns first (highest priority)
        for pattern in BLOCKED_PATTERNS:
            if pattern.search(command):
                log.warning("Command blocked by pattern: %s", command)
                return SafetyTier.BLOCKED

        # Extract the base command (first word, strip sudo/env prefixes)
        base = self._extract_base_command(command)

        if base in BLOCKED_COMMANDS:
            return SafetyTier.BLOCKED

        # Pipe chains: classify by the most dangerous component
        if "|" in command or "&&" in command or ";" in command:
            return self._classify_compound(command)

        # systemctl: check subcommand
        if base == "systemctl":
            return self._classify_systemctl(command)

        if base in AUTO_COMMANDS:
            return SafetyTier.AUTO

        if base in CONFIRM_COMMANDS:
            return SafetyTier.CONFIRM

        # Unknown command → confirm (err on the side of caution)
        return SafetyTier.CONFIRM

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        """Execute the command."""
        command = arguments.get("command", "").strip()
        timeout = min(arguments.get("timeout", 30), 300)

        if not command:
            return ToolResult(
                call_id="",
                name=self.name,
                content="Error: empty command",
                success=False,
            )

        safety = self.classify_safety(arguments)
        log.info("Command classified: %s → %s", command, safety.value)

        if safety == SafetyTier.BLOCKED:
            log.warning("Blocked dangerous command: %s", command)
            return ToolResult(
                call_id="",
                name=self.name,
                content=f"Command blocked by safety classifier: {command}",
                success=False,
            )

        try:
            log.debug("Executing: %s (timeout=%ds)", command, timeout)
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            stdout = result.stdout
            stderr = result.stderr

            # Truncate if too large
            if len(stdout) > MAX_OUTPUT_BYTES:
                stdout = stdout[:MAX_OUTPUT_BYTES] + f"\n... (truncated at {MAX_OUTPUT_BYTES} bytes)"
            if len(stderr) > MAX_OUTPUT_BYTES:
                stderr = stderr[:MAX_OUTPUT_BYTES] + f"\n... (truncated at {MAX_OUTPUT_BYTES} bytes)"

            output_parts = []
            if stdout:
                output_parts.append(stdout)
            if stderr:
                output_parts.append(f"[stderr]\n{stderr}")
            if result.returncode != 0:
                output_parts.append(f"[exit code: {result.returncode}]")

            content = "\n".join(output_parts) if output_parts else "(no output)"

            if result.returncode == 0:
                log.info("Command succeeded: %s", command)
            else:
                log.warning("Command failed (exit %d): %s", result.returncode, command)

            return ToolResult(
                call_id="",
                name=self.name,
                content=content,
                success=result.returncode == 0,
            )

        except subprocess.TimeoutExpired:
            log.warning("Command timed out after %ds: %s", timeout, command)
            return ToolResult(
                call_id="",
                name=self.name,
                content=f"Command timed out after {timeout} seconds: {command}",
                success=False,
            )
        except OSError as e:
            log.error("Command execution error: %s — %s", command, e)
            return ToolResult(
                call_id="",
                name=self.name,
                content=f"Command execution error: {e}",
                success=False,
            )

    def _extract_base_command(self, command: str) -> str:
        """Extract the base command name, stripping sudo/env/path prefixes."""
        parts = command.split()
        idx = 0
        while idx < len(parts):
            word = parts[idx]
            # Skip sudo and env prefixes
            if word in ("sudo", "env"):
                idx += 1
                # Skip env VAR=val pairs
                if word == "env":
                    while idx < len(parts) and "=" in parts[idx]:
                        idx += 1
                continue
            # Skip sudo flags
            if word.startswith("-") and idx > 0 and parts[idx - 1] == "sudo":
                idx += 1
                continue
            # Strip path prefix
            return word.rsplit("/", 1)[-1]
        return ""

    def _classify_compound(self, command: str) -> SafetyTier:
        """Classify a compound command (pipes, &&, ;) by worst component."""
        # Split on pipe, &&, ;
        parts = re.split(r"\||\&\&|;", command)
        worst = SafetyTier.AUTO
        for part in parts:
            part_args = {"command": part.strip()}
            tier = self.classify_safety(part_args)
            if tier == SafetyTier.BLOCKED:
                return SafetyTier.BLOCKED
            if tier == SafetyTier.CONFIRM:
                worst = SafetyTier.CONFIRM
        return worst

    def _classify_systemctl(self, command: str) -> SafetyTier:
        """Classify systemctl by subcommand."""
        parts = command.split()
        # Find the subcommand (skip systemctl and flags)
        for part in parts[1:]:
            if part.startswith("-"):
                continue
            if part == "sudo":
                continue
            if part in SYSTEMCTL_AUTO_SUBS:
                return SafetyTier.AUTO
            return SafetyTier.CONFIRM
        return SafetyTier.AUTO  # bare 'systemctl' = list-units
