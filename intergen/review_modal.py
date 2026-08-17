# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""D-008 RFC §7 review UX — provenance gate hold_for_review handler.

When the dispatcher gate holds a tool call for review, the user must
decide whether the action proceeds. RFC §7 specifies the modal contents
and the three button outcomes:

  - Allow once             → execute this specific action only
  - Allow for conversation → record approval in per-conv trust state
                             (RFC §7.2 symmetric trust state); subsequent
                             dispatches of this tool + source within this
                             conversation are auto-allowed
  - Deny                   → refuse; report back to the LLM as a tool
                             error so it can adjust

Two delivery paths per RFC §7.2:

  - **zenity modal (primary)** — when the GNOME shell session is active
    (DISPLAY or WAYLAND_DISPLAY set + screen unlocked), present a
    synchronous zenity --question dialog. zenity is chosen over a
    Gtk.AlertDialog wrapped in GLib.MainLoop because the dispatcher
    can be invoked from any thread (router worker, D-Bus method handler,
    CLI subcommand) and subprocess-based dialogs sidestep the
    main-thread + event-loop constraints of in-process GTK.

  - **libnotify fallback** — when the session is locked / headless /
    zenity unavailable, post a critical libnotify alert via notify-send
    + poll session-active in 5s intervals; if session returns active
    inside the timeout window, re-prompt via the zenity path. RFC §7.2
    timeout: held actions expire after one hour with implicit Deny.

Exports `make_review_callback(source_attribution, excerpt, reasoning)`
which returns a 2-arg (call, decision) → str closure suitable for
tool_registry.execute()'s `review_callback` parameter. The closure
captures the per-dispatch context (source URL, ingress excerpt, LLM
reasoning) that the dispatcher passes alongside the ToolCall +
DispatchDecision.

Return strings match the tool_registry.execute() contract:
"allow_once" | "allow_conversation" | "deny" | "deny_conversation".
The v1.0 zenity modal exposes 3 buttons (Allow once / Allow for this
conversation / Deny) per RFC §7 wireframe; "deny_conversation" is
reachable only via the dispatcher's record_user_decision path (not the
modal), and is preserved here as a valid return shape for callers that
build their own UX.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from typing import Callable

from intergen import consent_dialog
from intergen.consent_dialog_sanitize import tier_effect_sentence
from intergen.interfaces.provenance import DispatchDecision
from intergen.interfaces.types import ToolCall

logger = logging.getLogger(__name__)

# RFC §7.2 implicit-Deny timeout for libnotify fallback (one hour).
FALLBACK_TIMEOUT_SECONDS = 3600

# How often the libnotify fallback re-checks session-active state while
# waiting for the user to unlock. 5s balances responsiveness against
# subprocess overhead of repeated gdbus calls.
FALLBACK_POLL_INTERVAL_SECONDS = 5.0


def _ensure_display_env() -> None:
    """Recover DISPLAY/WAYLAND_DISPLAY from the --user manager environment if
    they are absent from this process's env.

    The daemon (systemd --user intergen.service, WantedBy=default.target) can
    start at boot BEFORE the graphical session imports DISPLAY/WAYLAND_DISPLAY
    into the --user manager, so the daemon process inherits neither — and then
    _session_active() below sees no display and every held action degrades to
    the buttonless notify-send fallback (a D-008 consent dead-end the user
    cannot act on) even though a graphical session is present. That was the
    boot-ordering bug behind the "needs review" popup with nothing to click.

    _session_active() is evaluated LAZILY at review time, by which point the
    user is interacting, so the session env has been imported and
    `systemctl --user show-environment` carries it; copy it into this process
    so the zenity modal can render. Best-effort: any failure leaves the env
    untouched and the caller falls back as before.
    """
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return
    systemctl = shutil.which("systemctl")
    if systemctl is None:
        return
    try:
        result = subprocess.run(
            [systemctl, "--user", "show-environment"],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, OSError):
        return
    if result.returncode != 0:
        return
    for line in result.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep and key in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY") \
                and not os.environ.get(key):
            os.environ[key] = value


def _session_active() -> bool:
    """Return True when the user's desktop session is reachable.

    Two-stage check:
      1. DISPLAY or WAYLAND_DISPLAY must be set (otherwise headless) — first
         self-healing them from the --user manager env if the daemon started
         before the session imported them (_ensure_display_env).
      2. The freedesktop ScreenSaver D-Bus method GetActive must report
         not-locked (best-effort — if the D-Bus call itself fails we
         assume the session is active so a flaky lock-check does not
         silently route everything to the fallback).
    """
    _ensure_display_env()
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    gdbus = shutil.which("gdbus")
    if gdbus is None:
        return True
    try:
        result = subprocess.run(
            [
                gdbus, "call", "--session",
                "--dest", "org.freedesktop.ScreenSaver",
                "--object-path", "/org/freedesktop/ScreenSaver",
                "--method", "org.freedesktop.ScreenSaver.GetActive",
            ],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.debug("ScreenSaver D-Bus check failed: %s — assuming active", e)
        return True
    if result.returncode != 0:
        return True
    # gdbus returns "(true,)" or "(false,)"; conservative: treat any
    # non-"true" output as unlocked.
    return "true" not in result.stdout.lower()


def review_header(decision: DispatchDecision) -> str:
    """The modal's first line: what class of action is being asked about.

    Added 2026-08-12. The line was previously the fixed string "InterGen wants
    to run a system action you did not directly request." — an assertion about
    both the effect (a system action) and the origin (not directly requested)
    that was derived from nothing. A read-only call that reached this modal was
    described to the user as a system action, and a call the user made directly
    was described as one they had not.

    Both halves are now read off the decision the gate actually produced. An
    unclassified tier keeps the severe wording: a missing classification must
    never be what softens a prompt.
    """
    effect = tier_effect_sentence(
        getattr(getattr(decision, "risk_tier", None), "value", None)
    )
    provenance = getattr(decision.effective_provenance, "value", "")
    if provenance == "user_direct":
        origin = "InterGen wants to run an action you asked for."
    elif provenance == "user_implied":
        origin = ("InterGen wants to run an action it inferred from what you "
                  "said — you did not ask for it directly.")
    else:
        origin = ("InterGen wants to run an action that came from content it "
                  "read, not from you.")
    return f"{origin} {effect}"


def _format_modal_body(
    call: ToolCall,
    decision: DispatchDecision,
    source_attribution: str,
    excerpt: str,
    reasoning: str,
) -> str:
    """Render the RFC §7 modal body text.

    Truncates excerpt + reasoning at 400 chars so the modal stays a
    reasonable size regardless of ingress content length; full source
    + full reasoning live in the audit log per RFC §9.
    """
    lines = [
        f"Tool:        {call.name}",
        f"Arguments:   {call.arguments}",
        f"Provenance:  {decision.effective_provenance.value}",
    ]
    if decision.needs_pkexec:
        lines.append(
            "Privilege:   this action runs as administrator — after you "
            "click Allow, the system will ask for your password"
        )
    if source_attribution:
        lines.append(f"Source:      {source_attribution}")
    if decision.reason:
        lines.append(f"Gate reason: {decision.reason}")
    if excerpt:
        snippet = excerpt.strip()
        if len(snippet) > 400:
            snippet = snippet[:400] + "..."
        lines.append("")
        lines.append("Excerpt that triggered this action:")
        lines.append(f"  {snippet}")
    if reasoning:
        snippet = reasoning.strip()
        if len(snippet) > 400:
            snippet = snippet[:400] + "..."
        lines.append("")
        lines.append("LLM reasoning:")
        lines.append(f"  {snippet}")
    return "\n".join(lines)


def _prompt_review_zenity(
    call: ToolCall,
    decision: DispatchDecision,
    source_attribution: str,
    excerpt: str,
    reasoning: str,
) -> str | None:
    """Synchronous zenity --text-info modal, body fed over stdin (keeps the
    review text out of the process argv, which any local process can list).
    Returns None if zenity is unavailable so the caller can route to the
    libnotify fallback.

    Button mapping:
      - --ok-label "Allow once"             → exit 0, empty stdout
      - --extra-button "Allow for this..."  → exit 1, button text on stdout
      - --cancel-label "Deny" / Esc / close → exit 1, empty stdout
      - --timeout expiry (unanswered)       → exit 5, Deny (RFC §7.2 —
        the same one-hour implicit-Deny bound as the libnotify path)
    """
    zenity = shutil.which("zenity")
    if zenity is None:
        logger.warning("zenity not found — routing to libnotify fallback")
        return None
    body = _format_modal_body(
        call, decision, source_attribution, excerpt, reasoning
    )
    header = review_header(decision)
    text = f"{header}\n\n{body}"
    try:
        result = subprocess.run(
            [
                zenity, "--text-info",
                "--title=InterGen — action review needed",
                "--width=720",
                "--height=480",
                f"--timeout={FALLBACK_TIMEOUT_SECONDS}",
                "--ok-label=Allow once",
                "--cancel-label=Deny",
                "--extra-button=Allow for this conversation",
            ],
            input=text,
            capture_output=True, text=True,
            timeout=FALLBACK_TIMEOUT_SECONDS + 60,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "Review dialog unanswered after %d seconds — implicit Deny per RFC §7.2",
            FALLBACK_TIMEOUT_SECONDS + 60,
        )
        return "deny"
    except OSError as e:
        logger.error("zenity invocation failed: %s — implicit Deny", e)
        return "deny"
    if result.returncode == 5:
        logger.warning(
            "Review dialog unanswered after %d seconds — implicit Deny per RFC §7.2",
            FALLBACK_TIMEOUT_SECONDS,
        )
        return "deny"
    if result.returncode == 0:
        return "allow_once"
    stdout = result.stdout.strip()
    if "Allow for this conversation" in stdout:
        return "allow_conversation"
    return "deny"


def _prompt_review_libnotify(
    call: ToolCall,
    decision: DispatchDecision,
    source_attribution: str,
    excerpt: str,
    reasoning: str,
) -> str:
    """libnotify-backed fallback per RFC §7.2.

    Posts a critical notification + waits up to FALLBACK_TIMEOUT_SECONDS
    for the session to become active, then re-prompts via zenity. If the
    timeout elapses the action implicit-denies (RFC §7.2 one-hour
    implicit-Deny). If notify-send itself is unavailable we cannot even
    surface the held action to the user, so we implicit-Deny immediately
    rather than executing without consent.
    """
    notify_send = shutil.which("notify-send")
    if notify_send is None:
        logger.warning(
            "notify-send not found — implicit Deny per RFC §7.2 "
            "(no surface to inform the user a held action is waiting)"
        )
        return "deny"
    # The fallback's whole purpose is to wait out a LOCKED/INACTIVE session and
    # re-prompt zenity once it returns. If zenity is not installed at all, that
    # re-prompt can never succeed — looping on it every poll for the full
    # FALLBACK_TIMEOUT_SECONDS (one hour) is a pure wedge: a CLI/headless `ask`
    # hangs the entire window for nothing. Detect the absent-zenity case once,
    # surface the held action via the notification, and implicit-Deny promptly
    # (no interactive GUI consent path exists, so waiting buys nothing).
    zenity_available = shutil.which("zenity") is not None
    summary = "InterGen — action review needed"
    body = _format_modal_body(
        call, decision, source_attribution, excerpt, reasoning
    )
    try:
        subprocess.run(
            [
                notify_send, "--app-name=intergen",
                "--urgency=critical",
                "--expire-time=0",
                summary, body,
            ],
            check=False, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.error("notify-send failed: %s — implicit Deny", e)
        return "deny"
    if not zenity_available:
        logger.warning(
            "zenity not installed — no interactive GUI consent path for held "
            "action %s; notified the user and implicit-Deny (not looping for "
            "%ds). Install zenity to enable the approval modal.",
            call.name, FALLBACK_TIMEOUT_SECONDS,
        )
        return "deny"
    deadline = time.monotonic() + FALLBACK_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _session_active():
            logger.info(
                "Session returned active for held action %s — re-prompting",
                call.name,
            )
            zenity_result = _prompt_review_zenity(
                call, decision, source_attribution, excerpt, reasoning
            )
            if zenity_result is not None:
                return zenity_result
        time.sleep(FALLBACK_POLL_INTERVAL_SECONDS)
    logger.warning(
        "Review for %s expired after %ds — implicit Deny per RFC §7.2",
        call.name, FALLBACK_TIMEOUT_SECONDS,
    )
    return "deny"


def prompt_review(
    call: ToolCall,
    decision: DispatchDecision,
    source_attribution: str = "",
    excerpt: str = "",
    reasoning: str = "",
) -> str:
    """Top-level review prompt — routes to zenity modal or libnotify
    fallback per RFC §7 + §7.2.
    """
    if _session_active():
        # Log the path BEFORE the (synchronous, blocking) modal: a held action's
        # 3-button dialog blocks waiting for a click, so an after-the-fact log would
        # never fire without interaction. Logging here puts the "session active →
        # render" proof in the journal immediately — which is how the cold-boot
        # consent validation reads it over SSH (no one at the framebuffer to click).
        logger.info(
            "consent: session active — rendering the branded GTK 3-button review "
            "dialog (zenity fallback ready; boot-daemon DISPLAY self-heal OK)")
        # Primary: the branded GTK binary (subprocess-isolated, scrubbed env). A
        # real rendered decision — INCLUDING deny — is returned as-is; we only fall
        # through to zenity when the binary could not render at all (return None),
        # never re-prompting after the user already decided.
        gtk_result = consent_dialog.run_review_dialog(
            call, decision, source_attribution, excerpt, reasoning
        )
        if gtk_result is not None:
            return gtk_result
        logger.warning(
            "consent: branded GTK review dialog did not render — falling back to "
            "the zenity 3-button modal")
        result = _prompt_review_zenity(
            call, decision, source_attribution, excerpt, reasoning
        )
        if result is not None:
            return result
        # zenity unavailable/failed despite an active session — _prompt_review_zenity
        # already logged the specific reason; fall through to the fallback below.
    else:
        # NO interactive session: the held action degrades to the buttonless critical
        # notification — a D-008 consent DEAD-END (the user can neither approve NOR
        # deny until the 1h implicit-Deny). On a normal desktop boot this must NOT
        # happen: _ensure_display_env() should have recovered the session at review
        # time. Log it LOUDLY (requirement 2026-06-28 — degraded consent/
        # teaching states must be impossible to miss); a DEFECT to root-cause, not rest.
        logger.warning(
            "consent: NO active session — routing the held action to the buttonless "
            "libnotify fallback (D-008 dead-end; implicit Deny in 1h). On a normal "
            "desktop this is a DEFECT: the boot-daemon DISPLAY self-heal should have "
            "recovered the session.")
    return _prompt_review_libnotify(
        call, decision, source_attribution, excerpt, reasoning
    )


def make_review_callback(
    source_attribution: str = "",
    excerpt: str = "",
    reasoning: str = "",
) -> Callable[[ToolCall, DispatchDecision], str]:
    """Build a 2-arg (call, decision) → str closure for
    ToolRegistry.execute()'s review_callback parameter.

    source_attribution + excerpt + reasoning describe the context that
    motivated the held call; they are captured in the closure since the
    registry's callback signature carries only (call, decision). The
    router constructs this closure per-turn from the ingress context it
    accumulated alongside the LLM tool-call emission.
    """
    def _callback(call: ToolCall, decision: DispatchDecision) -> str:
        return prompt_review(
            call, decision, source_attribution, excerpt, reasoning
        )
    return _callback
