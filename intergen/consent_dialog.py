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
  allow/send; everything else is a refusal. For a review, the refusal says what
  happened: the deny code is the person's own Deny ("deny"), the deadline is
  review_timed_out, and a crash, a signal or an unmapped code is review_failed —
  the registry records "deny" as the person's, so nothing else may borrow it.
  For a consent, every one of them is "not sent". A binary that never rendered →
  return None so the caller falls back to zenity (the user is never left
  without a prompt).
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
from typing import Callable

from intergen import consent_dialog_proto as proto
from intergen.consent_dialog_sanitize import describe_gate_action

logger = logging.getLogger(__name__)

# What _run_dialog answers when the dialog was on screen past its deadline and
# was killed. Not an exit status (no process returns it), so a caller can tell a
# dialog nobody answered from a decision.
DEADLINE_EXPIRED = -10000

# What _run_dialog answers when the caller's stop withdrew the dialog: the
# request it asked about ended before anyone answered (on the web surface, a
# door that ends the conversation it was asked from), so the dialog was killed
# rather than left asking a question nobody is waiting on. Not an exit status
# either, and never None, which would open the fallback in its place.
WITHDRAWN = -10001

# How often a dialog waiting on its answer reads the caller's stop. A dialog
# with no stop to read waits as it always has, in one wait to its deadline.
WITHDRAW_POLL_SECONDS = 0.1


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


def _run_dialog(spec: dict, cmd: list[str] | None = None,
                should_stop: Callable[[], bool] | None = None) -> int | None:
    """Spawn the branded binary for `spec` and return its decision exit code, or
    None if it never rendered (caller falls back to zenity).

    `cmd` is an injection seam for tests (defaults to the real binary).

    `should_stop`, when given, is the caller's stop: a callable that returns
    True once the request the dialog asks about has ended. It is read while
    the dialog renders and while it waits for the answer; once it is set the
    dialog is killed and WITHDRAWN is returned.

    Returns:
      * None                         — render never happened (spawn error, GTK
                                       init failure, no marker before timeout) →
                                       the user saw nothing; fall back to zenity.
      * proto.EXIT_*  (an int)       — the dialog rendered and exited with it.
      * DEADLINE_EXPIRED             — the dialog rendered and nobody answered it
                                       before the deadline; it was killed.
      * WITHDRAWN                    — the caller's stop was set before anyone
                                       answered; the dialog was killed.
                                       Any non-affirmative int is a refusal.
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
        if should_stop is not None and should_stop():
            return _withdraw(proc)
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
        if should_stop is None:
            proc.wait(timeout=proto.POST_RENDER_DEADLINE_SECONDS)
        elif _stopped_before_an_answer(proc, should_stop):
            return _withdraw(proc)
    except subprocess.TimeoutExpired:
        logger.warning(
            "consent dialog: no decision within %.0fs — not answered, nothing "
            "allowed or sent (fail-closed)",
            proto.POST_RENDER_DEADLINE_SECONDS,
        )
        _kill(proc)
        return DEADLINE_EXPIRED
    return proc.returncode


def _stopped_before_an_answer(proc: subprocess.Popen,
                              should_stop: Callable[[], bool]) -> bool:
    """Wait for the dialog's answer as proc.wait(timeout=the deadline) does,
    reading the caller's stop between short waits: True once the stop is set
    with the dialog still open, False once the dialog has exited, and
    TimeoutExpired at the deadline."""
    decide_by = time.monotonic() + proto.POST_RENDER_DEADLINE_SECONDS
    while True:
        try:
            proc.wait(timeout=min(WITHDRAW_POLL_SECONDS,
                                  max(0.0, decide_by - time.monotonic())))
            return False
        except subprocess.TimeoutExpired:
            if time.monotonic() >= decide_by:
                raise
        if should_stop():
            return True


def _withdraw(proc: subprocess.Popen) -> int:
    """Take down a dialog whose request has ended (the caller's stop is set)."""
    logger.info("consent dialog: the request it asked about has ended — the "
                "dialog was withdrawn; nothing allowed or sent")
    _kill(proc)
    return WITHDRAWN


# ── Public API used by the two modals ────────────────────────────────────────

def run_review_dialog(
    call,
    decision,
    source_attribution: str = "",
    excerpt: str = "",
    reasoning: str = "",
) -> str | None:
    """Render the tool-call review gate via the branded binary.

    Returns "allow_once" / "allow_conversation" / "deny" for the person's own
    answer, the registry's review_timed_out when the dialog went unanswered past
    its deadline, review_failed for a crash, a signal or an unmapped exit, or
    None if the binary could not render (caller falls back to zenity). Any
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

    from intergen.tool_registry import REVIEW_FAILED, REVIEW_TIMED_OUT

    code = _run_dialog(spec)
    if code is None:
        return None
    if code == proto.EXIT_REVIEW_ALLOW_ONCE:
        return "allow_once"
    if code == proto.EXIT_REVIEW_ALLOW_CONVERSATION:
        return "allow_conversation"
    if code == proto.EXIT_DENY:
        return "deny"  # the person's Not now, Esc, or closing the window
    if code == DEADLINE_EXPIRED:
        return REVIEW_TIMED_OUT
    # A crash, a signal or an unmapped exit: nobody answered. Nothing runs, and
    # the record does not name the person for it.
    return REVIEW_FAILED


def run_consent_dialog(content: str, provider: str, reason: str = "",
                       should_stop: Callable[[], bool] | None = None,
                       ) -> bool | None:
    """Render the show-before-send egress consent via the branded binary.

    Returns True only on an explicit Send, False on any rendered non-send
    (Cancel / Esc / close / crash / DEADLINE_EXPIRED / over-size) or a dialog
    the caller's stop withdrew (WITHDRAWN, rendered or not: no fallback may
    open after it), or None if the binary could not render (caller falls back
    to zenity). `should_stop` is as for _run_dialog."""
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

    # A caller that hands no stop makes exactly the call it made before.
    stop_kwargs = {} if should_stop is None else {"should_stop": should_stop}
    code = _run_dialog(spec, **stop_kwargs)
    if code is None:
        return None
    return code == proto.EXIT_CONSENT_SEND
