# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Daemon-side launcher for the branded GTK4 consent dialog.

`review_modal` and `consent_modal` call into here to render their consent
surfaces via the branded binary (`intergen.consent_dialog_gtk`) instead of
zenity. This module owns the spawn-side half of the security contract
(`docs/research/branding/consent-dialog/`); the binary owns the render-side half.

What this enforces:

- **Transport (§A / inv transport):** the spec goes to the child on **stdin** as
  one JSON object — never argv (so `/proc/<pid>/cmdline` leaks nothing, closing the
  pre-existing review_modal argv leak). The payload + tool arguments are JSON
  string values a real parser reads as opaque fields.
- **Scrubbed child env + no-new-privs (§C / inv 10):** the child inherits ONLY an
  allowlist of display/session vars (dropping the LD_PRELOAD / GTK_MODULES /
  GIO_* code-injection family by construction) and is launched with
  PR_SET_NO_NEW_PRIVS.
- **Pre-render watchdog + bounded wait (§F / inv 11):** fail FAST to zenity if the
  binary never signals it rendered; wait patiently (bounded by the deadline →
  deny) once it has.
- **Fail-closed mapping:** only the explicit affirmative exit codes become an
  allow/send; everything else (deny code, crash, signal, timeout, unmapped) →
  deny/cancel. A binary that never rendered → return None so the caller falls back
  to zenity (the user is never left without a prompt).
- **Never truncate the egress payload (§E):** an over-MAX consent payload is denied
  here before spawn (defense in depth; the binary guards it too).
"""

from __future__ import annotations

import json
import logging
import os
import select
import subprocess
import sys
import threading
import time

from intergen import consent_dialog_proto as proto
from intergen.consent_dialog_sanitize import describe_gate_action

logger = logging.getLogger(__name__)


def _scrubbed_env() -> dict[str, str]:
    """Allowlist-only child environment (§C). Inherits ONLY the display/session
    vars the renderer needs; the code-injection family (LD_PRELOAD,
    LD_LIBRARY_PATH, GTK_MODULES, GTK_PATH, GIO_MODULE_DIR, GIO_EXTRA_MODULES,
    GTK_IM_MODULE, GSETTINGS_BACKEND, PYTHONPATH, …) is dropped by virtue of not
    being on the list. PATH is fixed, never inherited."""
    env: dict[str, str] = {}
    for key in proto.ENV_ALLOWLIST:
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    env["PATH"] = proto.SAFE_PATH
    return env


def _kill(proc: subprocess.Popen) -> None:
    try:
        proc.kill()
        proc.wait(timeout=5)
    except Exception:
        pass


def _run_dialog(spec: dict, cmd: list[str] | None = None) -> int | None:
    """Spawn the branded binary for `spec` and return its decision exit code, or
    None if it never rendered (caller falls back to zenity).

    `cmd` is an injection seam for tests (defaults to the real binary).

    Returns:
      * None                         — render never happened (spawn error, GTK
                                       init failure, no marker before timeout) →
                                       the user saw nothing; fall back to zenity.
      * proto.EXIT_*  (an int)       — the dialog rendered and exited (or was
                                       killed post-render at the deadline → EXIT_DENY).
                                       Any non-affirmative int the caller maps to deny.
    """
    if cmd is None:
        cmd = [sys.executable, "-m", "intergen.consent_dialog_gtk"]
    data = json.dumps(spec)
    try:
        # NOTE: no-new-privs is set by the BINARY as its own first action (before
        # GTK), NOT via preexec_fn here — preexec_fn runs post-fork/pre-exec in this
        # multithreaded daemon and is fork-unsafe (it could deadlock the child).
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_scrubbed_env(),
            text=True,
        )
    except OSError as e:
        logger.warning("consent dialog: spawn failed (%s) — falling back to zenity", e)
        return None

    # Feed stdin from a thread so a large payload can never deadlock against our
    # own stdout read (pipe-buffer backpressure).
    def _feed() -> None:
        try:
            assert proc.stdin is not None
            proc.stdin.write(data)
            proc.stdin.close()
        except Exception:
            pass

    threading.Thread(target=_feed, daemon=True).start()

    # ── Watchdog: wait for the RENDERED marker; fail FAST if it never comes ──
    assert proc.stdout is not None
    rendered = False
    deadline = time.monotonic() + proto.PRE_RENDER_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        readable, _, _ = select.select(
            [proc.stdout], [], [], min(0.5, max(0.0, remaining))
        )
        if readable:
            line = proc.stdout.readline()
            if line == "":  # EOF — child exited without rendering
                break
            if line.strip() == proto.RENDERED_MARKER:
                rendered = True
                break
            # any other stdout chatter: ignore and keep waiting
        elif proc.poll() is not None:
            break  # process gone, no marker
    if not rendered:
        logger.warning(
            "consent dialog: binary did not render within %.0fs — falling back to zenity",
            proto.PRE_RENDER_TIMEOUT_SECONDS,
        )
        _kill(proc)
        return None

    # ── Decision: wait patiently for the human, bounded by the deadline ─────
    try:
        proc.wait(timeout=proto.POST_RENDER_DEADLINE_SECONDS)
    except subprocess.TimeoutExpired:
        logger.warning(
            "consent dialog: no decision within %.0fs — deny/cancel (fail-closed)",
            proto.POST_RENDER_DEADLINE_SECONDS,
        )
        _kill(proc)
        return proto.EXIT_DENY
    return proc.returncode


# ── Public API used by the two modals ────────────────────────────────────────

def run_review_dialog(
    call,
    decision,
    source_attribution: str = "",
    excerpt: str = "",
    reasoning: str = "",
) -> str | None:
    """Render the tool-call review gate via the branded binary.

    Returns "allow_once" / "allow_conversation" / "deny" on a rendered decision,
    or None if the binary could not render (caller falls back to zenity). Any
    unexpected error → None (fall back to the proven path — fail safe)."""
    try:
        raw_args = getattr(call, "arguments", "")
        # Deterministic action headline from the structured (tool, action) — the
        # OBJECT of consent in plain words. None (no template) → "" so the binary
        # falls closed to the verbatim tool: args form.
        headline = describe_gate_action(getattr(call, "name", ""), raw_args)
        spec = {
            proto.K_MODE: proto.MODE_REVIEW,
            proto.K_TOOL: str(getattr(call, "name", "?")),
            proto.K_HEADLINE: str(headline or ""),
            # Cap review CONTEXT fields (tool args + excerpt + reasoning — none is an
            # egress payload) so the dialog stays a sane size; symmetric with the
            # excerpt/reasoning 400-char caps. A too-large render would otherwise
            # trip the 20s watchdog → zenity (fail-safe), but cap up front.
            proto.K_ARGUMENTS: str(raw_args)[:2000],
            proto.K_PROVENANCE: str(decision.effective_provenance.value),
            proto.K_REASON: str(decision.reason or ""),
            proto.K_SOURCE: str(source_attribution or ""),
            proto.K_NEEDS_PKEXEC: bool(getattr(decision, "needs_pkexec", False)),
            # The CLASS of action, so the dialog can say what it does
            # instead of inferring an effect from its origin. "" when the
            # decision carries no tier — the renderer treats that as severe.
            proto.K_RISK_TIER: str(
                getattr(getattr(decision, "risk_tier", None), "value", "") or ""),
            # Cap review CONTEXT (not an egress payload) so the dialog stays a sane
            # size — mirrors review_modal._format_modal_body's 400-char cap.
            proto.K_EXCERPT: (excerpt or "").strip()[:400],
            proto.K_REASONING: (reasoning or "").strip()[:400],
        }
    except Exception as e:
        logger.warning("consent dialog: could not build review spec (%s) — fallback", e)
        return None

    code = _run_dialog(spec)
    if code is None:
        return None
    if code == proto.EXIT_REVIEW_ALLOW_ONCE:
        return "allow_once"
    if code == proto.EXIT_REVIEW_ALLOW_CONVERSATION:
        return "allow_conversation"
    return "deny"  # EXIT_DENY, crash, signal, unmapped → fail-closed deny


def run_consent_dialog(content: str, provider: str, reason: str = "") -> bool | None:
    """Render the show-before-send egress consent via the branded binary.

    Returns True only on an explicit Send, False on any rendered non-send
    (Cancel / Esc / close / crash / deadline / over-size), or None if the binary
    could not render (caller falls back to zenity)."""
    try:
        # §E — never truncate the egress payload: too-large = fail-closed deny,
        # never a truncated render (defense in depth; the binary guards it too).
        if len(content.encode("utf-8", "surrogatepass")) > proto.MAX_PAYLOAD_BYTES:
            logger.warning(
                "consent dialog: outbound payload exceeds %d bytes — denying "
                "(cannot show 100%% of it; show-before-send forbids a truncated view)",
                proto.MAX_PAYLOAD_BYTES,
            )
            return False
        spec = {
            proto.K_MODE: proto.MODE_CONSENT,
            proto.K_PROVIDER: str(provider or ""),
            proto.K_REASON: str(reason or ""),
            proto.K_PAYLOAD: content,
        }
    except Exception as e:
        logger.warning("consent dialog: could not build consent spec (%s) — fallback", e)
        return None

    code = _run_dialog(spec)
    if code is None:
        return None
    return code == proto.EXIT_CONSENT_SEND
