# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Write or edit files — generates diff for user confirmation."""

from __future__ import annotations

import difflib
import logging
import os
from pathlib import Path
from typing import Any

from intergen.interfaces.tool import BaseTool
from intergen.interfaces.types import SafetyTier, ToolResult, ToolSchema

log = logging.getLogger(__name__)

# Exact paths the AI tool must NEVER write — credential/identity stores and the
# kernel/initramfs. Hard BLOCKED in EVERY context, root included (a write here
# corrupts the trust base regardless of consent). A human edits these by hand.
PROTECTED_PATHS = frozenset({
    "/etc/passwd", "/etc/shadow", "/etc/group", "/etc/gshadow",
    "/etc/sudoers", "/boot/vmlinuz", "/boot/initramfs",
})

# AI-5 (BLEND, decided 2026-05-30) — gate-side half: directory
# prefixes whose contents are danger-EQUIVALENT to the exact PROTECTED_PATHS —
# pure privilege-escalation / boot-integrity vectors. A write whose resolved
# path is under one of these is BLOCKED even as root, closing the exact-match
# narrowness (/etc/sudoers blocked but /etc/sudoers.d/evil was not; /boot/vmlinuz
# blocked but the bootloader config elsewhere under /boot/ was not).
BLOCKED_PREFIXES = (
    "/etc/sudoers.d/",   # a drop-in here is identical in power to /etc/sudoers
    "/boot/",            # bootloader + kernel + initramfs: boot-chain integrity
)

# AI-5 — prefixes that are sensitive system locations but ALSO legitimate
# AI-assisted config-edit targets (Prime Directive: don't break the user editing
# their own system config with help). These are CONFIRM-tier (gate-side: a write
# under them is a deliberate, human-reviewed action, never a silent pass) AND, on
# the root side (the euid backstop half), an unprivileged (euid != 0) write to
# one is refused so it must traverse the REVIEWED privileged path: write_file is
# PRIVILEGED_STATE_CHANGING, so a genuine sensitive write reaches execute() in
# root context (euid 0) via privileged_dispatch only after the human review modal
# + dispatch-token. Both halves read the SAME list so they cannot diverge.
SENSITIVE_PREFIXES = (
    "/etc/", "/usr/lib/systemd/", "/usr/lib/modules/", "/usr/share/",
)

def _canon_prefix(prefix: str) -> str:
    """Canonicalize a never-list prefix the SAME way a candidate path is
    normalized (expanduser + resolve), preserving the trailing slash the prefix
    semantics rely on.

    Without this the home anchor ("~/.config/intergen/") would be expanduser-only
    while the candidate the caller passes IS resolved — so a pre-existing symlink
    in the parent (a Stow/chezmoi-symlinked ~/.config is the common case) makes the
    resolved candidate diverge from the unresolved prefix and slip the floor. That
    is exactly the location-side asymmetry the signed-manifest matcher closes (WC's
    scan-config carry-forward + matcher finding); the interim floor must match it so
    BOTH sides of every prefix comparison are canonical (the symmetry rule). A
    resolve error falls back to the expanduser-only form — never weaker than before.
    """
    had_trailing = prefix.endswith("/") and prefix != "/"
    try:
        canon = str(Path(prefix).expanduser().resolve())
    except (OSError, RuntimeError, ValueError):
        canon = os.path.expanduser(prefix)
    if had_trailing and not canon.endswith("/"):
        canon += "/"
    return canon


# Sentinel decision #5 + the destructive-policy manifest's `system_ai` category:
# InterGen's own config + state is AI-IMMUTABLE — the AI tool path must NEVER write
# it (a human hand-edits or uses the authenticated GUI, neither of which is this
# tool). BLOCKED even as root. This closes the window where the config the daemon
# now reads at startup (sentinel/escalation/providers + the dispatch key, memory,
# governance) could be self-rewritten by the AI through the user-override config
# that SUPERSEDES the system one.
#
# These three prefixes mirror the operator-SIGNED manifest's system_ai set and now
# serve as the DEFENSE-IN-DEPTH FLOOR: the signed-manifest destructive_policy
# matcher (wired into _classify_path below) is the primary, comprehensive never-list,
# but if the manifest cannot be established from a trusted source (missing / bad
# signature / wrong key — load_policy returns None) these critical InterGen-config
# paths stay BLOCKED regardless. Canonicalized (expanduser + resolve) so the home
# anchor cannot diverge from the resolved candidate.
AI_IMMUTABLE_PREFIXES = tuple(_canon_prefix(p) for p in (
    "~/.config/intergen/",
    "/var/lib/intergen/",
    "/var/log/intergen/",
    # InterGen's own substrate is system-critical (gating model §3): InterGen
    # may never rewrite its own code, panel, pins, units, or runner — the
    # no-self-modification keystone. Canonical list lives in intergen/zones.py
    # (_INTERGEN_SUBSTRATE); these floor entries must stay in sync with it.
    "/usr/lib/python3.14/site-packages/intergen/",
    "/usr/share/intergen/",
    "/usr/share/gnome-shell/extensions/intergen@intergenos.org/",
    "/usr/bin/intergen-model-setup-runner",
))


def _is_sensitive_prefix(path: Path) -> bool:
    """True if the resolved path is under a system-sensitive prefix (CONFIRM
    tier + root-side euid-backstop scope)."""
    p = str(path)
    return any(p.startswith(prefix) for prefix in SENSITIVE_PREFIXES)


# Signed-manifest never-list (Sentinel decision #5). The destructive_policy matcher
# is the primary, comprehensive system_ai set, established ONLY from the operator-
# signed manifest (load_policy verifies a detached OpenPGP signature against the
# pinned operator fingerprint and returns None on any doubt — fail-closed). It is
# loaded ONCE, lazily (gpg verify is not free, and the manifest does not change at
# runtime). A failed load leaves the matcher absent and the interim
# AI_IMMUTABLE_PREFIXES floor in force (defense in depth). Tests inject a fake
# policy by setting _policy_cache + _policy_loaded directly.
_policy_cache = None
_policy_loaded = False
# PI-D hardening — True iff the never-list manifest was PRESENT but failed to
# verify (tamper / corruption), as opposed to legitimately absent. A genuine
# verify failure must DOWNGRADE LOUDLY, not silently: this flag drives the
# alert-level log + the user-visible banner so a tamper-induced downgrade of the
# authoritative never-list cannot hide behind a healthy-looking self-test.
_policy_untrusted = False

# The loud banner prepended to every write that proceeds while the signed
# never-list is UNTRUSTED. It rides on the tool's own result text so the user
# sees it on the action itself, not only in a log they may never read.
_UNTRUSTED_BANNER = (
    "SECURITY ALERT: the signed destructive-policy never-list could NOT be "
    "verified (possible tampering or corruption). Only the interim AI-immutable "
    "floor is protecting system paths right now — the full identity, auth, and "
    "boot never-list is NOT being enforced. Re-verify the manifest "
    "(/usr/share/intergen/destructive-policy-manifest.json) and the keyring "
    "(/etc/pkm/trusted.gpg) before trusting the system.\n\n"
)


def _manifest_policy():
    """Return the loaded DestructivePolicy (signed manifest), or None. Loaded once."""
    global _policy_cache, _policy_loaded, _policy_untrusted
    if not _policy_loaded:
        try:
            from intergen.destructive_policy import PolicyLoad, load_policy_status
            _policy_cache, status = load_policy_status()
            if _policy_cache is not None:
                log.info("destructive-policy never-list loaded (signed manifest "
                         "v%s)", getattr(_policy_cache, "manifest_version", "?"))
            elif status == PolicyLoad.ABSENT:
                # No manifest artifact at all — the documented defense-in-depth
                # floor fallback (benign on a from-source/dev box). Quiet, as before.
                log.warning("destructive-policy manifest absent; write_file "
                            "enforcing the interim AI-immutable floor only")
            else:
                # UNTRUSTED — the manifest IS present but its signature/key could
                # not be trusted: tamper or corruption. The authoritative
                # never-list has downgraded to the interim floor. FAIL LOUD
                # (PI-D hardening, WC wedge 1): alert-level here + a user-visible
                # banner on every write that proceeds.
                _policy_untrusted = True
                log.critical(
                    "SECURITY: destructive-policy never-list is PRESENT but "
                    "UNTRUSTED (signature/key verification failed — possible "
                    "tampering or corruption). The authoritative never-list has "
                    "DOWNGRADED to the interim AI-immutable floor; the full "
                    "identity/auth/privilege never-list is NOT enforced. "
                    "Re-verify /usr/share/intergen/destructive-policy-manifest.json "
                    "and /etc/pkm/trusted.gpg.")
        except Exception as exc:  # noqa: BLE001 — never break write_file on a load error
            # An unexpected loader error is itself an integrity failure — treat
            # it as untrusted (loud), not a benign absence.
            _policy_untrusted = True
            log.critical("SECURITY: destructive-policy load raised (%s); treating "
                         "the never-list as UNTRUSTED (interim floor only).",
                         type(exc).__name__)
            _policy_cache = None
        _policy_loaded = True
    return _policy_cache


def _never_list_untrusted() -> bool:
    """True iff the signed never-list was PRESENT but failed verification
    (tamper / corruption). Ensures the (cached) load has run, then reads the flag."""
    _manifest_policy()
    return _policy_untrusted


def _classify_path(path: Path) -> SafetyTier:
    """Shared write_file path policy over a RESOLVED absolute path.

    Used by BOTH classify_safety() (gate-side tier) and execute() (root-side
    enforcement) so the two cannot diverge — defense in depth at the privilege
    boundary. The path MUST already be expanduser().resolve()'d by the caller so
    symlink / .. tricks are normalized before the prefix comparison.

    BLOCKED  — exact protected file, or under a danger-equivalent prefix.
    CONFIRM  — under a sensitive system prefix (live, human-reviewed), or any
               other write (every write is at least CONFIRM).
    """
    p = str(path)
    if p in PROTECTED_PATHS:
        return SafetyTier.BLOCKED
    for prefix in BLOCKED_PREFIXES:
        if p == prefix.rstrip("/") or p.startswith(prefix):
            return SafetyTier.BLOCKED
    # Decision #5 — the signed-manifest never-list (PRIMARY). The destructive_policy
    # matcher is the full operator-signed system_ai + boot/identity set; it
    # canonicalizes both pattern and candidate, so symlink/.. tricks cannot launder
    # the comparison. is_protected returns a match (BLOCKED) or None.
    policy = _manifest_policy()
    if policy is not None and policy.is_protected(p) is not None:
        return SafetyTier.BLOCKED
    # Defense-in-depth FLOOR — InterGen's own config + state stays AI-immutable even
    # if the signed manifest could not be loaded (load_policy returned None). These
    # canonicalized prefixes are a subset of the manifest's system_ai, so the two
    # never disagree; the floor only ever adds protection, never removes it.
    for prefix in AI_IMMUTABLE_PREFIXES:
        if p == prefix.rstrip("/") or p.startswith(prefix):
            return SafetyTier.BLOCKED
    return SafetyTier.CONFIRM


class WriteFileTool(BaseTool):
    """Write or edit a file, showing a diff before confirming."""

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return (
            "Create a new file or change an existing file's CONTENTS. For an "
            "existing file a unified diff is shown for review before "
            "confirming. Use this only for writing file text — not for running "
            "shell commands (use run_command) or installing software (use "
            "manage_packages)."
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

        # AI-5 BLEND — gate-side policy, re-enforced root-side via the SHARED
        # _classify_path so the two layers cannot diverge. BLOCKED covers the
        # exact protected files AND the danger-equivalent prefixes
        # (/etc/sudoers.d/, /boot/); refused in EVERY context, root included.
        # resolve() above already collapsed symlinks / .. so a symlink into a
        # blocked path cannot launder the comparison.
        if _classify_path(path) == SafetyTier.BLOCKED:
            return ToolResult(
                call_id="", name=self.name,
                content=(
                    f"Blocked: {path} is a protected system file or under a "
                    "danger-equivalent prefix (refused in every context, "
                    "root included)."
                ),
                success=False,
            )

        # AI-5 root-side euid backstop: a system-sensitive path must come through
        # the reviewed privileged path (human modal + dispatch token -> root via
        # privileged_dispatch). If we are asked to write a sensitive path but are
        # NOT root, this call did not traverse that path — refuse rather than
        # self-approve a system write in the user context.
        if _is_sensitive_prefix(path) and os.geteuid() != 0:
            log.warning(
                "Refusing unprivileged write to sensitive path %s "
                "(must go through the reviewed privileged dispatch path)", path,
            )
            return ToolResult(
                call_id="", name=self.name,
                content=(
                    f"Blocked: {path} is under a system-sensitive prefix and "
                    "can only be written through the reviewed privileged path "
                    "(human approval + dispatch token), not a direct write."
                ),
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

        # PI-D hardening: if the signed never-list is PRESENT but UNTRUSTED
        # (tamper / corruption), this write proceeded under the degraded interim
        # floor only. Make that impossible to miss — prepend the loud banner so a
        # tamper-induced downgrade surfaces on the action itself.
        if _never_list_untrusted():
            result_text = _UNTRUSTED_BANNER + result_text

        log.info("Wrote %s", path)
        return ToolResult(
            call_id="", name=self.name,
            content=result_text,
            success=True,
        )

    def classify_safety(self, arguments: dict[str, Any]) -> SafetyTier:
        """Gate-side tier via the SHARED _classify_path policy.

        BLOCKED for the exact protected files + danger-equivalent prefixes
        (/etc/sudoers.d/, /boot/); CONFIRM for everything else — including the
        sensitive prefixes, which additionally get the root-side euid backstop
        in execute(). Sharing _classify_path keeps the gate-side tier and the
        root-side enforcement from diverging.
        """
        path_str = arguments.get("path", "")
        if not path_str:
            return SafetyTier.BLOCKED

        return _classify_path(Path(path_str).expanduser().resolve())
