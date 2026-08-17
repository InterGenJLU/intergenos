# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Shell command execution with tiered safety classification.

Safety tiers:
  auto    — read-only commands (ls, cat, grep, df, ps, uname, etc.)
  confirm — write commands (mkdir, cp, mv, chmod, chown, etc.)
  blocked — destructive commands (disk/filesystem wipes, recursive deletes, etc.)

When uncertain, defaults to 'confirm'. The classifier errs on the side
of caution — a write command misclassified as 'auto' is dangerous,
but a read command misclassified as 'confirm' is merely annoying.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
from typing import Any

from intergen.interfaces.tool import BaseTool
from intergen.interfaces.types import SafetyTier, ToolResult, ToolSchema
from intergen.safety import classify_command

log = logging.getLogger(__name__)

# Shell control / substitution / redirection metacharacters. The run_command
# tool executes a SINGLE argv command with shell=False (M8-1 leg 0) — it never
# spawns a shell — so an input carrying any of these is declined fail-closed
# rather than mis-executed (a shell line the user meant as a pipeline would
# otherwise pass its operators as literal args to the first program). This is
# defense-in-depth clarity on top of the structural guarantee: with shell=False,
# execve cannot reach a shell, so a string that joins two commands with a
# separator cannot run its second command on its own — classifier present or
# absent. The classify_command gate stays as the second, independent layer.
_SHELL_CONSTRUCT_CHARS = frozenset("|&;<>()$`\n\r")

# AI-4: the safety tier tables + destructive-pattern denylist + compound
# decomposition that used to live here now live in the single strong
# classifier intergen.safety.classify_command (a strict superset of the
# prior per-tool checks). classify_safety() below delegates to it so there is
# one classifier to audit and no weaker second copy to drift.

MAX_OUTPUT_BYTES = 65536  # 64 KB — truncate beyond this


class RunCommandTool(BaseTool):
    """Execute a single command (shell=False) with tiered safety classification."""

    @property
    def name(self) -> str:
        return "run_command"

    @property
    def description(self) -> str:
        return (
            "Execute a single command on this system — the general-purpose tool "
            "for any system query or action that has no dedicated tool of its "
            "own. Use this to list printers (lpstat -p -d), show hardware and "
            "devices, inspect files, check the network, and similar. Runs ONE "
            "command with no shell: pipes, redirection, ; && &, command "
            "substitution and subshells are not supported — ask for one plain "
            "command at a time. Read-only commands run automatically. Write "
            "commands require user confirmation. Destructive commands are blocked."
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
        """Classify command safety tier.

        AI-4: delegates to the single strong classifier in intergen.safety
        (classify_command), which is a strict superset of the per-tool checks
        that used to live here — base-command + destructive-pattern blocks,
        compound decomposition, and the AUTO/CONFIRM tables. Keeping one
        classifier means one place to audit and no weaker second copy to drift.
        """
        command = arguments.get("command", "").strip()
        if not command:
            return SafetyTier.BLOCKED
        return classify_command(command)

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
                # Structured signal so the router's synthesis hop can detect a
                # HARD safety block deterministically (not by string-matching
                # content) and skip narrating it — otherwise the model fabricates
                # success on a blocked destructive command (the dd-wipe finding).
                blocked=True,
            )

        # M8-1 leg 0 — the executor no longer speaks shell. Decline any input
        # carrying a shell construct fail-closed: this tool runs ONE argv
        # command with shell=False, so pipes / redirection / ; / && / & /
        # command substitution / subshells / newlines have no meaning here.
        # Structurally, removing the shell is what closes the classifier-vs-shell
        # parse disagreement (a benign leading token followed by a second command
        # joined by a separator the classifier under-counted): the second command
        # can never run on its own. This decline is the loud, honest surface for it.
        offending = sorted({c for c in command if c in _SHELL_CONSTRUCT_CHARS})
        if offending:
            shown = ", ".join(repr(c) for c in offending)
            log.warning("Refused shell-construct command (shell-free executor): %s", command)
            return ToolResult(
                call_id="",
                name=self.name,
                content=(
                    "This tool runs a single command with no shell, so shell "
                    "constructs are not supported (found: " + shown + "). Pipes, "
                    "redirection, ; && & , command substitution $(), subshells, "
                    "and newlines can't be used here — ask for one plain command "
                    "at a time."
                ),
                success=False,
                # Same HARD-block signal as the classifier path: nothing ran, so
                # the synthesis hop must not narrate a success (no fabrication).
                blocked=True,
            )

        # Tokenize into an argv vector — no shell parsing, no word-splitting a
        # shell would do beyond quotes. shlex raises on unbalanced quotes; that
        # is unparseable input, refused fail-closed.
        try:
            argv = shlex.split(command)
        except ValueError as e:
            log.warning("Refused unparseable command: %s — %s", command, e)
            return ToolResult(
                call_id="",
                name=self.name,
                content=f"Could not parse the command (unbalanced quotes?): {e}",
                success=False,
                blocked=True,
            )
        if not argv:
            return ToolResult(
                call_id="",
                name=self.name,
                content="Error: empty command",
                success=False,
            )

        try:
            log.debug("Executing: %s (timeout=%ds)", argv, timeout)
            result = subprocess.run(
                argv,
                shell=False,
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
