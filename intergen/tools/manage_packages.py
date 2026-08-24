# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
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
import os
import re
import shutil
import subprocess
from typing import Any

from intergen.interfaces.tool import BaseTool
from intergen.interfaces.types import SafetyTier, ToolResult, ToolSchema

#: The package manager as the system ships it. A MUTATION names this path
#: rather than resolving "pkm" through PATH: this branch runs as root, and a
#: root process that resolves a bare program name is trusting whatever PATH
#: happens to say. Read-only actions run as the user and keep using the
#: resolved path, because there is no privilege there to misplace.
PKM_VENDOR_PATH = "/usr/bin/pkm"

#: Actions that change the system. These are exactly the actions that reach
#: this tool through the privileged dispatcher, and the only ones the root
#: invariant below applies to.
MUTATING_SUBCOMMANDS = ("install", "remove", "uninstall", "update", "upgrade")

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

# Header pkm prints for a package listing, e.g. "  Installed packages (824):"
# (pkm/cli.py cmd_list). The count lives in the parens — the most-asked
# question is "how many?", so it leads the model summary.
_LIST_HEADER_RE = re.compile(
    r"^\s*(?P<kind>\w[\w ]*?) packages \((?P<count>\d+)\):", re.MULTILINE
)
# How many package names to sample into the model summary.
_LIST_SAMPLE = 10


def summarize_package_list(content: str) -> str | None:
    """Build a concise model-facing summary of a `pkm list` dump.

    pkm prints a header line carrying the exact count, then one indented
    "name version [tier] — desc" line per package (pkm/cli.py cmd_list).
    The full dump is ~42 KB for 824 packages — far too large for the local
    2B to ingest on the synthesis hop (G3-22: 319 s, zero tokens). This
    returns a few-hundred-byte summary that leads with the exact count and a
    small name sample, so the model can answer "how many packages?" fast and
    state the count exactly. The caller keeps the full dump as ToolResult
    .content for the user-facing transcript.

    Returns None when the output does not look like a pkm listing (unexpected
    format / error text) — the caller then leaves model_summary=None and the
    4000-char floor in continue_after_tool_call still guards the model.
    """
    header = _LIST_HEADER_RE.search(content)
    if header is None:
        return None
    kind = header.group("kind").strip().lower()
    count = int(header.group("count"))

    # Package-entry lines are indented under the header; the first whitespace-
    # delimited token on each is the package name (name is left-justified to
    # 30 cols, so split() yields the name first).
    names: list[str] = []
    for line in content.splitlines():
        if line.startswith("    ") and line.strip():
            names.append(line.split()[0])
            if len(names) >= _LIST_SAMPLE:
                break

    sample = ", ".join(names)
    more = count - len(names)
    more_note = f" (+{more} more)" if more > 0 else ""
    # Purely factual, salient-first — clean for BOTH the model synthesis AND the
    # user-facing tool card that displays this line (D-2). The "report counts
    # exactly / don't enumerate long lists / full output already shown to the
    # user" steering lives generically in LLMRouter._SYNTHESIS_RULES (rule 7),
    # so it is NOT repeated here where it would leak model plumbing into the UI.
    return f"{count} {kind} packages. Sample: {sample}{more_note}."


class ManagePackagesTool(BaseTool):
    """Manage packages via pkm (InterGenOS package manager)."""

    @property
    def name(self) -> str:
        return "manage_packages"

    @property
    def description(self) -> str:
        return (
            "Manage installed SOFTWARE PACKAGES (applications and libraries) on "
            "this system using pkm. Supports: list, search, info, install, "
            "remove, verify — all operating on software packages ONLY. This does "
            "NOT list printers, hardware, devices, services, or files; for any of "
            "those, use run_command instead. "
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

        # THE ROOT INVARIANT (added 2026-08-24).
        #
        # A mutating action arrives here only after the privileged dispatcher
        # has carried it across the boundary once and verified the approval
        # token, so this code is running as root. That used to be guaranteed by
        # construction — the builder assembled ["pkexec", "pkm", ...] and the
        # transition happened here. That construction was removed because it
        # bought a second crossing with no PolicyKit action and no token; what
        # it left behind was an ASSUMPTION stated in a comment.
        #
        # This is that assumption turned into a check. If a mutating action
        # reaches this tool without root, the honest outcome is a refusal that
        # says so — not an unprivileged `pkm install` whose failure the user has
        # to decode. Read-only actions are deliberately outside the invariant:
        # they classify AUTO, run as the user by design, and reading your own
        # machine's state needs no privilege.
        if action in MUTATING_SUBCOMMANDS and os.geteuid() != 0:
            return ToolResult(
                call_id="", name=self.name,
                content=(
                    f"the package action '{action}' changes the system and must "
                    f"run as root, but this process is running as uid "
                    f"{os.geteuid()} (checked). It was not attempted. A "
                    f"state-changing package action reaches this tool through "
                    f"the privileged dispatcher, which is what makes it root; "
                    f"arriving here without that means the dispatch path was "
                    f"bypassed."
                ),
                success=False,
            )

        # Which pkm to run. A mutation names the vendor path; a read-only action
        # resolves it. See PKM_VENDOR_PATH.
        if action in MUTATING_SUBCOMMANDS:
            pkm_path = PKM_VENDOR_PATH
            if not (os.path.isfile(pkm_path) and os.access(pkm_path, os.X_OK)):
                return ToolResult(
                    call_id="", name=self.name,
                    content=(
                        f"the package manager is not present as an executable "
                        f"at {pkm_path} (checked), so '{action}' was not "
                        f"attempted."
                    ),
                    success=False,
                )
        else:
            pkm_path = shutil.which("pkm")
            if pkm_path is None:
                return ToolResult(
                    call_id="", name=self.name,
                    content=(
                        "pkm is not installed on this system yet. "
                        "pkm is this system's native package manager — it needs to "
                        "be promoted from build tool to system tool before package "
                        "management is available."
                    ),
                    success=False,
                )

        # Build the pkm command
        cmd = self._build_command(action, package, query, pkm_path)
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
            # G3-22 real fix: `list` returns the full ~42 KB/824-package dump,
            # which times out the 2B on synthesis. Hand the MODEL a concise
            # structured summary (count + sample); the USER keeps the full
            # `content` unchanged. Other actions (info/search/verify) are
            # already small/salient → model_summary stays None (pass-through).
            model_summary = None
            if action == "list" and result.returncode == 0:
                model_summary = summarize_package_list(content)
            return ToolResult(
                call_id="", name=self.name,
                content=content,
                success=result.returncode == 0,
                model_summary=model_summary,
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

    def _build_command(self, action: str, package: str, query: str,
                       pkm: str = "pkm") -> list[str] | None:
        """Build the pkm command list.

        `pkm` is the program to invoke, resolved by the caller — absolute in
        every case the caller produces. It is a parameter rather than a
        constant so the mutating and read-only paths can differ in WHICH pkm
        they name without this builder deciding privilege policy.
        """
        if action == "list":
            return [pkm, "list"]
        elif action == "search":
            if not query:
                return None
            return [pkm, "search", query]
        elif action in ("info", "verify"):
            if not package:
                return None
            return [pkm, action, package]
        # State-changing package actions need root, and by the time this builder
        # runs they already HAVE it. manage_packages is on the privileged
        # allowlist and these actions classify CONFIRM, so the dispatcher routes
        # them through the transient unit -> pkexec -> the privileged runner ->
        # the root-side dispatcher, which verifies the human-approval token
        # before calling execute(). This code is running as root inside that
        # dispatcher.
        #
        # Decided 2026-08-24: build no second privilege transition here. It used
        # to construct ["pkexec", "pkm", ...], which bought nothing and cost a
        # crossing that had no PolicyKit action of its own, carried no approval
        # token binding it to what the person approved, and ran from the
        # environment the runner deliberately scrubbed — so it could not have
        # raised a prompt anyone could answer even if it tried. One approval,
        # one crossing, one gate.
        #
        # The read-only actions above (list/search/info/verify) never reach the
        # privileged path at all: they classify AUTO and run as the user, which
        # is correct — reading your own machine's state changes nothing.
        elif action in ("install", "remove", "uninstall"):
            if not package:
                return None
            return [pkm, action, package]
        elif action in ("update", "upgrade"):
            if package:
                return [pkm, "update", package]
            return [pkm, "update"]
        return None
