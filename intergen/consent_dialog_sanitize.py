# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Display-safety helpers for the consent dialog — visual integrity + secret aid.

Factored out of the GTK binary so the two security-critical, content-handling
functions are unit-testable WITHOUT a display or GTK (CI + adversarial review).
Pure stdlib (unicodedata + re); no GTK, no I/O, no network.

  * `sanitize_for_display` (§B / invariant 8) — makes reordering / hidden /
    non-printing characters VISIBLE so SHOWN == SENT *visually*. It NEVER changes
    what the daemon sends (that is the original bytes); it only governs what the
    human is shown, badging anything that could deceive the eye.
  * `detect_secret_ranges` (§5.5) — an AID, not a gate: conservative, local,
    deterministic offset ranges over the shown text, applied by the binary as
    buffer TAGS (never as text edits). A miss only weakens the aid.
"""

from __future__ import annotations

import re
import unicodedata

# Codepoints that can reorder or hide text with zero code execution — Trojan-
# Source (bidi) and zero-width hiding. Badged visibly rather than stripped so the
# human can see something was there.
_DANGEROUS_CODEPOINTS = {
    0x202A, 0x202B, 0x202C, 0x202D, 0x202E,          # LRE/RLE/PDF/LRO/RLO (bidi)
    0x2066, 0x2067, 0x2068, 0x2069,                  # LRI/RLI/FSI/PDI (isolates)
    0x200E, 0x200F, 0x061C,                          # LRM/RLM/ALM (marks)
    0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF,          # zero-width + BOM
}
# Unicode categories always badged: Cc (control), Cf (format), Co (private use),
# Cn (unassigned), Zl/Zp (line/paragraph separators). Ordinary spaces (Zs) and
# printables pass; \n and \t are explicitly preserved.
_BADGE_CATEGORIES = {"Cc", "Cf", "Co", "Cn", "Zl", "Zp"}


def sanitize_with_ranges(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Return (shown, badge_ranges): `text` with deceptive / hidden / non-printing
    characters rendered visibly as 〈U+XXXX〉 badges, AND the char-offset ranges (over
    the SHOWN string) of each inserted badge. The ranges let the renderer tag REAL
    badges distinctly so literal payload text mimicking a badge cannot be mistaken
    for a system-inserted one (anti-spoof). Pure function; leaves \\n and \\t intact.

    Known-accepted residuals (do NOT break SHOWN==SENT — bytes shown == bytes sent):
    homoglyphs/confusables and combining-mark (Zalgo) stacking are NOT badged — they
    affect human interpretation only, not which bytes are sent; the secret AID may
    miss a homoglyph- or zero-width-split secret, which is within "aid, not gate"."""
    out: list[str] = []
    ranges: list[tuple[int, int]] = []
    pos = 0  # char offset in the OUTPUT (shown) string
    for ch in text:
        if ch in ("\n", "\t"):
            out.append(ch)
            pos += 1
            continue
        cp = ord(ch)
        if cp in _DANGEROUS_CODEPOINTS or unicodedata.category(ch) in _BADGE_CATEGORIES:
            badge = f"〈U+{cp:04X}〉"
            ranges.append((pos, pos + len(badge)))
            out.append(badge)
            pos += len(badge)
        else:
            out.append(ch)
            pos += 1
    return "".join(out), ranges


def sanitize_for_display(text: str) -> str:
    """Convenience wrapper returning only the badged display text (for short
    chrome/label fields). The payload view uses `sanitize_with_ranges` so it can
    tag real badges distinctly."""
    return sanitize_with_ranges(text)[0]


# Provenance → badge CSS class. The review gate fires precisely when provenance is
# NOT clean, so the badge must signal the REAL trust level, never a blanket green.
_PROVENANCE_BADGE = {
    "user_direct": "igc-badge-direct",      # green  — you asked for it directly
    "user_implied": "igc-badge-warn",       # amber  — inferred, not commanded
    "ingress_derived": "igc-badge-danger",  # red    — influenced by external content
}


def provenance_badge_class(provenance: object) -> str:
    """CSS class for the provenance badge, keyed to the real trust level.
    Unknown / unexpected provenance → danger (fail-safe risk signal)."""
    return _PROVENANCE_BADGE.get(str(provenance), "igc-badge-danger")


# Plain-language risk breakdown for the review gate, keyed to provenance. Returns
# (severity, headline, detail) — the dialog turns the jargon classification into
# words a first-time user understands: WHAT this is and WHAT allowing it carries.
# severity ∈ {"ok","warn","danger"} drives the callout color. Unknown → danger.
_REVIEW_RISK = {
    # Each entry answers ONLY "who asked for this". What the action DOES is the
    # tier's sentence, appended by review_risk_copy — the user_direct detail
    # used to assert "It still changes your system", which was untrue whenever
    # a read reached this surface.
    "user_direct": (
        "ok",
        "You asked InterGen to do this directly.",
        "Go ahead only if it's what you intended.",
    ),
    "user_implied": (
        "warn",
        "InterGen inferred you wanted this — you didn't ask for it directly.",
        "Allow it only if it matches what you meant; otherwise choose Not now.",
    ),
    "ingress_derived": (
        "danger",
        "This was requested by content InterGen read — a web page, a file, or a "
        "tool's output — NOT by you.",
        "Allowing it lets that outside content run this action on your machine. If "
        "you didn't expect this, choose Not now.",
    ),
}


# What each CLASS of action does, in the user's words. Keyed on the
# ToolRiskTier value string so this module keeps its no-intergen-imports
# property. Added 2026-08-12: the copy below described where a request CAME
# FROM and then asserted what it DOES ("It still changes your system"), which
# is a claim about the class — a claim nothing in this module could check. A
# read-only call that reached the gate was therefore described to the user as
# changing the system. The two are now separate: provenance supplies the origin
# sentence, the tier supplies the effect sentence.
_TIER_EFFECT = {
    "read_only": "It only reads information from your system and changes nothing.",
    "user_scope_state_changing":
        "It changes something in your own account or session.",
    "privileged_state_changing":
        "It changes system software or settings and needs administrator approval.",
}

# An unclassified action is treated as the most severe case. A missing tier
# must never be the thing that softens a prompt.
_TIER_EFFECT_UNKNOWN = (
    "InterGen could not classify what this changes — treat it as a change to "
    "your system."
)


def tier_effect_sentence(risk_tier: object) -> str:
    """One sentence naming what a class of action does. Unknown → severe."""
    return _TIER_EFFECT.get(str(risk_tier), _TIER_EFFECT_UNKNOWN)


def review_risk_copy(provenance: object, risk_tier: object) -> tuple[str, str, str]:
    """(severity, headline, detail) plain-language breakdown for a review gate.

    `provenance` answers "who asked for this" and sets the severity; the
    ToolRiskTier value string in `risk_tier` answers "what does it do" and is
    appended to the detail. Unknown provenance → a generic danger message, and
    an unknown tier → the severe effect sentence (both fail-safe).

    risk_tier is REQUIRED rather than defaulted on purpose: a caller that has
    no tier must say so explicitly (pass None) instead of silently getting
    wording that omits the effect.
    """
    p = str(provenance)
    effect = tier_effect_sentence(risk_tier)
    if p in _REVIEW_RISK:
        severity, headline, detail = _REVIEW_RISK[p]
        return severity, headline, f"{detail} {effect}"
    return (
        "danger",
        "This request has an origin InterGen doesn't recognize.",
        "Treat it as untrusted — allow it only if you are certain it is safe. "
        f"{effect}",
    )


# Plain-language ACTION HEADLINE for the review gate — the OBJECT of consent
# stated in words the user reads first ("Install package: htop"), instead of
# forcing them to parse `Tool manage_packages` + {'action':'install',...} out of
# the card. Built ONLY from the (tool, action) pair and its key argument, by a
# fixed template — NEVER model-generated: the consent surface must state exactly
# what will execute. The verbatim tool+args box stays below as the always-
# verifiable original (r29 normalized-first + always-verifiable, applied to
# consent). Returns None when no template matches, so the caller fails CLOSED to
# the verbatim `tool: args` form rather than inventing a friendly label. The
# returned text still carries untrusted arg bytes, so the renderer sanitises it
# for display like any content field.
_PKG_VERBS = {
    "install": "Install", "remove": "Remove", "uninstall": "Remove",
    "update": "Update", "upgrade": "Upgrade",
    "list": "List", "list-installed": "List installed", "search": "Search",
    "info": "Show info for", "verify": "Verify",
}
_SVC_VERBS = {
    "start": "Start", "stop": "Stop", "restart": "Restart", "reload": "Reload",
    "enable": "Enable", "disable": "Disable", "mask": "Mask", "unmask": "Unmask",
    "status": "Show status of", "is-active": "Check whether running:",
    "is-enabled": "Check whether enabled:",
}


def describe_gate_action(tool: object, arguments: object) -> str | None:
    """Deterministic plain-language headline for a review-gate action, or None
    when no template matches (caller falls closed to the verbatim form).

    Pure + template-only (no model, no I/O). `arguments` is the structured
    tool-call dict; a non-dict or unknown (tool, action) yields None."""
    if not isinstance(arguments, dict):
        return None
    tool = str(tool or "")
    action = str(arguments.get("action") or "").strip().lower()

    if tool == "manage_packages":
        pkg = str(arguments.get("package") or arguments.get("query") or "").strip()
        verb = _PKG_VERBS.get(action)
        if not verb:
            return None
        if action in ("list", "list-installed"):
            return "List installed packages"
        return f"{verb} package: {pkg}" if pkg else f"{verb} packages"

    if tool == "manage_services":
        svc = str(arguments.get("service") or arguments.get("unit") or "").strip()
        if action == "daemon-reload":
            return "Reload the systemd manager configuration"
        if action in ("list-units", "list-unit-files"):
            return "List system services"
        verb = _SVC_VERBS.get(action)
        if not verb:
            return None
        return f"{verb} service: {svc}" if svc else f"{verb} a service"

    if tool == "run_command":
        cmd = str(arguments.get("command") or "").strip()
        return f"Run command: {cmd}" if cmd else None

    if tool == "write_file":
        path = str(arguments.get("path") or "").strip()
        return f"Write file: {path}" if path else None

    if tool == "take_screenshot":
        return "Take a screenshot"

    return None


# Conservative secret patterns. Local + deterministic; no network. Order-
# independent; spans are returned over the shown text and applied as tags.
_SECRET_PATTERNS = [
    re.compile(r"(?i)\b(?:password|passwd|secret|token|api[_-]?key|auth)\b\s*[=:]\s*(\S+)"),
    re.compile(r"://[^/\s]*?:([^@/\s]{3,})@"),                         # creds in a URL
    re.compile(r"\b(?:sk|rk|ghp|gho|ghs|xox[baprs]|AKIA|ASIA|AIza)[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
]


def detect_secret_ranges(shown: str) -> list[tuple[int, int]]:
    """Best-effort secret spans (char offsets) over the shown text. Any error →
    [] (the aid degrades silently; the full payload is still shown)."""
    spans: list[tuple[int, int]] = []
    try:
        for pat in _SECRET_PATTERNS:
            for m in pat.finditer(shown):
                start, end = (m.span(1) if m.groups() else m.span(0))
                if end > start:
                    spans.append((start, end))
    except Exception:
        return []
    return spans
