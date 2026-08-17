# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Zone classifier for the InterGen gating model.

Implements the privilege-axis zone overlay of the canonical gating spec
(docs/security/intergen-gating-model.md): a filesystem / resource target is
Z1 (user space), Z2 (system config-state), or Z3 (system-critical + trust
anchor, INCLUDING InterGen's own substrate).

Z3 writes / state-changes are FORBIDDEN to InterGen — the no-self-modification
keystone (§3). The *user* may still do them manually; InterGen simply will not
be the one to touch them, and says so transparently (§6). This module is the
ONE place the Z3 boundary is defined so it cannot drift.

This is the PRIVILEGE axis only. Whether a (permitted) read's *content* may
carry injection or leak a secret is the separate content-trust axis (§1) and is
not decided here.
"""

from __future__ import annotations

from pathlib import Path

# ── Z3a: system-critical / trust anchor ────────────────────────────────────
# Modifying any of these can break the boot chain, the Secure Boot / verity
# trust anchor, system identity/auth, or disk integrity.
_Z3_SYSTEM_CRITICAL: tuple[str, ...] = (
    "/boot", "/efi",
    "/etc/shadow", "/etc/gshadow", "/etc/passwd", "/etc/group",
    "/etc/sudoers", "/etc/sudoers.d",
    "/etc/crypttab", "/etc/fstab",
    "/etc/pam.d", "/etc/polkit-1",
    "/etc/secureboot", "/var/lib/shim-signed",
    "/usr/lib/modules", "/lib/modules",
)

# ── Z3b: InterGen's OWN substrate ──────────────────────────────────────────
# Write-forbidden to InterGen so it can never be coerced into editing its own
# code, model, pins, units, or guardrails. An AI that cannot weaken itself.
_INTERGEN_SUBSTRATE: tuple[str, ...] = (
    "/usr/lib/python3.14/site-packages/intergen",
    "/usr/share/gnome-shell/extensions/intergen@intergenos.org",
    "/usr/share/intergen",
    "/var/lib/intergen",
    "/usr/bin/intergen-model-setup-runner",
    "/usr/lib/systemd/user/intergen.service",
    "/usr/lib/systemd/system/intergen.service",
    "/usr/share/polkit-1/actions/org.intergenos.intergen",
)

_Z3_PREFIXES: tuple[str, ...] = _Z3_SYSTEM_CRITICAL + _INTERGEN_SUBSTRATE

# Tool actions that are inherently Z3 by their very nature — they target the
# boot/secure-boot trust chain or InterGen's own governance / model / signing
# substrate. FORBIDDEN regardless of arguments and NOT user-authorizable: these
# are operator-deliberate, out-of-band actions, never the assistant's to take
# (gating model §3). Mirrors governance.OWNER_ONLY_ACTIONS so the gate and the
# forbidden set cannot drift.
_FORBIDDEN_TOOLS: frozenset[str] = frozenset({
    "modify_governance",
    "modify_model_files",
    "signing_key_operation",
    "modify_bootloader_chain",
    "modify_secure_boot",
})

# Read-only systemd actions — these never change state, so they are never
# forbidden even on an InterGen unit (the privilege axis frees reads, §5).
_SERVICE_READ_ACTIONS: frozenset[str] = frozenset({
    "status", "is-active", "is-enabled", "is-failed",
    "list-units", "list-unit-files", "list-timers", "show", "cat",
})


def _resolve(path: str | Path) -> str:
    """Normalize a path for prefix comparison without requiring it to exist
    (resolves `..`/`~`, collapses separators). Never raises."""
    try:
        return str(Path(path).expanduser().resolve(strict=False))
    except Exception:
        return str(path)


def is_z3_write_target(path: str | Path) -> bool:
    """True if writing/modifying ``path`` would touch a system-critical or
    InterGen-substrate location (Z3 — FORBIDDEN to InterGen)."""
    if not path:
        return False
    p = _resolve(path)
    for pref in _Z3_PREFIXES:
        if p == pref or p.startswith(pref.rstrip("/") + "/"):
            return True
    return False


# The transparent, anti-HAL refusal (gating model §6): state what, why, and
# that the user can still do it themselves. No tiers, no "blocked", no jargon.
_REFUSAL = (
    "I won't change the system's boot or security files — that trust chain is "
    "what keeps this machine yours, and I keep my own components inside it so I "
    "can't be talked into weakening myself. It's your machine, so you can do "
    "this yourself with administrator rights if you mean to; I just won't be "
    "the one to touch it."
)


def forbidden_reason(tool_name: str, arguments: dict) -> str | None:
    """If this action is a Z3 write / state-change (FORBIDDEN to InterGen per
    the gating model §3 & §5), return the transparent user-facing refusal
    (§6); otherwise return None.

    Covers the cleanly-determinable vectors: a file write whose target is Z3,
    and a state-changing systemd action on an InterGen unit. Arbitrary-shell
    Z3 writes via run_command are caught upstream by classify_command's
    destructive/critical → BLOCKED handling.
    """
    args = arguments or {}
    if tool_name in _FORBIDDEN_TOOLS:
        return _REFUSAL
    if tool_name == "write_file":
        if is_z3_write_target(args.get("path", "")):
            return _REFUSAL
        return None
    if tool_name == "manage_services":
        action = str(args.get("action", "")).lower()
        unit = str(args.get("unit") or args.get("service") or "").lower()
        if action and action not in _SERVICE_READ_ACTIONS and "intergen" in unit:
            return _REFUSAL
        return None
    return None
