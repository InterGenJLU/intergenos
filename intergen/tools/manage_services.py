# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Systemd service management — status/start/stop/enable/disable.

Safety tiers, derived from the action and never from the tool name:
  auto    — everything in AUTO_ACTIONS: status, is-active, is-enabled,
            is-failed, list-units, list-unit-files, list-timers,
            list-sockets, list-dependencies, show, cat
  confirm — everything in CONFIRM_ACTIONS: start, stop, restart, reload,
            enable, disable, mask, unmask, daemon-reload

ACTION_ALIASES maps colloquial spellings onto those canonical verbs before
either list is consulted, and the schema enum is built from all three sets, so
a value this tool accepts is a value it declares and vice versa. An action
outside the enum is refused by BaseTool.validate_arguments before the
dispatcher gate sees it.
"""

from __future__ import annotations

import logging
import re
import subprocess
from typing import Any

from intergen.interfaces.tool import BaseTool
from intergen.interfaces.types import SafetyTier, ToolResult, ToolSchema

log = logging.getLogger(__name__)

# systemctl list-units prints a "N loaded units listed." footer — the
# authoritative total. Used as ground truth for the structured summary.
_UNIT_FOOTER_RE = re.compile(r"^\s*(\d+) loaded units listed", re.MULTILINE)
# Known LOAD-column values; a line whose 2nd column is one of these is a real
# unit row (filters the header/legend/footer out of the parse).
_LOAD_STATES = frozenset({"loaded", "not-found", "bad-setting", "error", "masked"})
# ACTIVE states in salient order — "failed" surfaces first after the counts
# because "are any services broken?" is the diagnostic question.
_ACTIVE_ORDER = (
    "failed", "active", "activating", "reloading",
    "deactivating", "inactive", "maintenance",
)
_UNIT_SAMPLE = 8        # unit names sampled into the summary
_FAILED_SAMPLE = 8      # failed-unit names named explicitly

# Colloquial → canonical InterGenOS systemd unit names. Users (and the 2B)
# routinely say "ssh", but the unit on InterGenOS is sshd.service — without this,
# `systemctl status ssh` returns "Unit ssh.service could not be found" and a
# legitimate "is ssh running?" deflects. Applied at the tool boundary so it
# covers BOTH the deterministic service-name resolver and the model's own
# tool-call argument. Only verified InterGenOS unit names belong here.
_SERVICE_ALIASES = {
    "ssh": "sshd",
}


def summarize_service_list(content: str) -> str | None:
    """Build a concise, purely-factual model-facing summary of `systemctl
    list-units` output.

    The full listing is ~72 KB / 400+ unit rows — far too large for the local
    2B to ingest on the synthesis hop (same G3-22 failure mode as the package
    list: timeout, zero tokens). This returns a few-hundred-byte summary that
    leads with the exact unit count and the active/failed breakdown, then names
    any failed units (the diagnostic answer) and a small sample. The caller
    keeps the full listing as ToolResult.content for the user-facing transcript.

    Purely factual — no model-directed steering (that lives generically in
    LLMRouter._SYNTHESIS_RULES rule 7, so it never leaks into the D-2 tool card,
    per the manage_packages exemplar refinement eca08d9b).

    Returns None when the output does not look like a `list-units` listing
    (unexpected format / error text) — the caller then leaves model_summary=None
    and the 4000-char floor in continue_after_tool_call still guards the model.
    """
    lines = content.splitlines()
    # Locate the column header ("UNIT LOAD ACTIVE SUB DESCRIPTION").
    header_idx = None
    for i, line in enumerate(lines):
        toks = line.split()
        if toks[:3] == ["UNIT", "LOAD", "ACTIVE"]:
            header_idx = i
            break
    if header_idx is None:
        return None

    states: dict[str, int] = {}
    failed: list[str] = []
    names: list[str] = []
    for line in lines[header_idx + 1:]:
        if not line.strip():
            break  # blank line terminates the unit block (before the Legend)
        toks = line.split()
        if toks and toks[0] == "●":  # leading status glyph on some rows
            toks = toks[1:]
        if len(toks) < 4 or toks[1] not in _LOAD_STATES:
            continue
        name, active = toks[0], toks[2]
        states[active] = states.get(active, 0) + 1
        if active == "failed":
            failed.append(name)
        if len(names) < _UNIT_SAMPLE:
            names.append(name)

    if not states:
        return None

    footer = _UNIT_FOOTER_RE.search(content)
    total = int(footer.group(1)) if footer else sum(states.values())

    ordered = [f"{states[s]} {s}" for s in _ACTIVE_ORDER if states.get(s)]
    ordered += [f"{n} {s}" for s, n in states.items()
                if s not in _ACTIVE_ORDER and n]
    breakdown = ", ".join(ordered)

    failed_note = ""
    if failed:
        shown = ", ".join(failed[:_FAILED_SAMPLE])
        extra = len(failed) - _FAILED_SAMPLE
        failed_note = f" Failed: {shown}{f' (+{extra} more)' if extra > 0 else ''}."

    sample = ", ".join(names)
    more = total - len(names)
    more_note = f" (+{more} more)" if more > 0 else ""
    return (
        f"{total} systemd units loaded: {breakdown}.{failed_note} "
        f"Sample: {sample}{more_note}."
    )

AUTO_ACTIONS = frozenset({
    "status", "is-active", "is-enabled", "is-failed",
    "list-units", "list-unit-files", "show", "cat",
    "list-timers", "list-sockets", "list-dependencies",
})

CONFIRM_ACTIONS = frozenset({
    "start", "stop", "restart", "reload", "enable", "disable",
    "mask", "unmask", "daemon-reload",
})

# Colloquial → canonical systemctl verb, the action-level twin of
# _SERVICE_ALIASES above. Added 2026-08-12 for a measured defect: asked to list
# services, the model called this tool with action "list". manage_packages
# already accepts "list" as a read, so the two sibling tools disagreed on the
# spelling of the most basic query — and "list" is a verb systemd itself does
# not have ("Unknown command verb 'list'"). Because the action matched neither
# AUTO_ACTIONS nor CONFIRM_ACTIONS it took the unrecognised-action default of
# CONFIRM, which on this pkexec-escalating tool becomes the privileged tier, so
# a read raised an administrator-approval prompt for a command that could not
# have run.
#
# This map is an explicit, audited allowlist in the same shape as AUTO_ACTIONS:
# each entry names one canonical verb this tool already supports. Normalisation
# happens before classification, so an alias for a read classifies as a read.
# It is deliberately NOT a fuzzy matcher — an action outside the schema enum is
# still refused by validate_arguments rather than guessed at.
ACTION_ALIASES = {
    "list": "list-units",
    "list-services": "list-units",
    "list-files": "list-unit-files",
}


def canonical_action(action: object) -> str:
    """The canonical systemctl verb for a caller-supplied action string.

    Pure and total: anything unrecognised is returned unchanged (as a string)
    so classification and validation still see exactly what was asked for.
    """
    text = str(action or "").strip()
    return ACTION_ALIASES.get(text, text)


class ManageServicesTool(BaseTool):
    """Manage systemd services via systemctl."""

    @property
    def name(self) -> str:
        return "manage_services"

    @property
    def description(self) -> str:
        return (
            "Manage systemd SERVICES on this system: check status, start, "
            "stop, enable, or disable a named service. Status queries run "
            "automatically; state changes require confirmation. Use this only "
            "for systemd services — not for software packages (use "
            "manage_packages) or arbitrary shell commands (use run_command)."
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
                        # Every value this tool actually accepts, including
                        # the read-only verbs it always supported but never
                        # declared (is-failed, list-timers, …) and the aliases
                        # in ACTION_ALIASES. The enum is now enforced by
                        # BaseTool.validate_arguments, so a declared value must
                        # work and a working value must be declared.
                        "enum": sorted(
                            AUTO_ACTIONS | CONFIRM_ACTIONS
                            | set(ACTION_ALIASES)
                        ),
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
        """Classify based on the action.

        Aliases are canonicalised FIRST so a colloquial spelling of a read is
        classified as the read it resolves to — the same string execute() will
        run. An action this tool does not define still returns CONFIRM, but it
        no longer reaches this method: validate_arguments refuses an
        out-of-enum value before the dispatcher gate is consulted.
        """
        action = canonical_action(arguments.get("action", ""))
        if action in AUTO_ACTIONS:
            return SafetyTier.AUTO
        if action in CONFIRM_ACTIONS:
            return SafetyTier.CONFIRM
        return SafetyTier.CONFIRM

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        """Execute the systemctl action."""
        action = canonical_action(arguments.get("action", ""))
        service = arguments.get("service", "")
        user_mode = arguments.get("user_mode", False)

        # Canonicalize colloquial unit names to the real InterGenOS unit
        # (ssh -> sshd). Tolerate a trailing ".service" and any case; pass the
        # original through unchanged when there's no alias.
        if service:
            service = _SERVICE_ALIASES.get(
                service.lower().removesuffix(".service"), service)

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

        # State-changing actions on a SYSTEM service are privileged, but we do
        # NOT use `sudo`: the daemon runs in the user's session with no tty, so
        # `sudo` cannot prompt and would simply fail. Instead we run `systemctl`
        # as the user — systemd's own D-Bus path then raises PolicyKit's native
        # authentication dialog in the session ("Authenticate to manage system
        # services"). That is the OS-enforced AUTH-PROMPT of the gating model
        # (docs/security/intergen-gating-model.md §5): the consent block records
        # the user's intent, and polkit performs the actual privilege grant.
        # User-mode (`--user`) services belong to the user and need no elevation.
        is_privileged = action in CONFIRM_ACTIONS and not user_mode

        try:
            result = subprocess.run(
                # A privileged action blocks on the interactive polkit dialog,
                # so give the user time to authenticate.
                cmd, capture_output=True, text=True,
                timeout=120 if is_privileged else 30,
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

            # G3-22 structured returns: `list-units` is the ~72 KB/400+-row dump
            # that times out the 2B on synthesis (same class as manage_packages
            # list). Hand the MODEL a concise count-led summary; the USER keeps
            # the full listing in `content`. Other actions (status/show/cat) are
            # already small/salient → model_summary stays None (pass-through).
            model_summary = None
            if action == "list-units" and success:
                model_summary = summarize_service_list(content)

            return ToolResult(
                call_id="", name=self.name,
                content=content,
                success=success,
                model_summary=model_summary,
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
