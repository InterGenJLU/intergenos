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
    timeout: held actions expire after one hour, and nothing runs.

Exports `make_review_callback(source_attribution, excerpt, reasoning)`
which returns a 2-arg (call, decision) → str closure suitable for
tool_registry.execute()'s `review_callback` parameter. The closure
captures the per-dispatch context (source URL, ingress excerpt, LLM
reasoning) that the dispatcher passes alongside the ToolCall +
DispatchDecision.

Return strings match the tool_registry.execute() contract:
"allow_once" | "allow_conversation" | "deny" | "deny_conversation" for the
person's own answer, and the registry's words for a review nobody decided:
review_timed_out when the review went unanswered past its deadline, and
review_failed when it could not be shown or answered. "deny" is only ever the
person's Deny (the button, Esc, or closing the dialog): the registry records it
as theirs, so a timeout or a missing tool must never borrow it. Nothing runs on
any answer but an approval.
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
from intergen.tool_registry import (REVIEW_FAILED, REVIEW_TIMED_OUT,
                                    describe_call)

logger = logging.getLogger(__name__)

# RFC §7.2 timeout for the libnotify fallback (one hour); an expiry runs nothing.
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
    untouched and the caller falls back as before. The probe runs through the
    dialogs' bounded runner (consent_dialog.run_bounded), so a probe its
    two-second bound cannot end is reported with its pid, never waited on.
    """
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return
    systemctl = shutil.which("systemctl")
    if systemctl is None:
        return
    try:
        result = consent_dialog.run_bounded(
            [systemctl, "--user", "show-environment"], timeout=2,
            what="systemctl",
            consequence="it may still be running; the display variables are "
                        "left as they are",
        )
    except (subprocess.TimeoutExpired, OSError,
            consent_dialog.DialogNotTakenDown):
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
         silently route everything to the fallback). The call runs through
         the dialogs' bounded runner, as the probe in stage 1 does.
    """
    _ensure_display_env()
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    gdbus = shutil.which("gdbus")
    if gdbus is None:
        return True
    try:
        result = consent_dialog.run_bounded(
            [
                gdbus, "call", "--session",
                "--dest", "org.freedesktop.ScreenSaver",
                "--object-path", "/org/freedesktop/ScreenSaver",
                "--method", "org.freedesktop.ScreenSaver.GetActive",
            ],
            timeout=2, what="gdbus",
            consequence="it may still be running; the session is taken as "
                        "active",
        )
    except (subprocess.TimeoutExpired, OSError,
            consent_dialog.DialogNotTakenDown) as e:
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

    The act is the registry's ONE description of it
    (tool_registry.describe_call): its sentence, and the command line the
    tool's own builder returns, never the raw argument dict, which named
    "ssh" for sshd and hid a --yes or a --user that ran. A tool with no command
    line shows its sentence only.

    Truncates excerpt + reasoning at 400 chars so the modal stays a
    reasonable size regardless of ingress content length; full source
    + full reasoning live in the audit log per RFC §9.
    """
    act = describe_call(call)
    lines = [f"Tool:        {call.name}"]
    if act.what:
        lines.append(f"Action:      {act.what}")
    if act.command_line:
        lines.append(f"Command:     {act.command_line}")
    if not act.what and not act.command_line:
        # No act to describe (refused before any card in the registry): say
        # so rather than show the arguments as if they were the act.
        lines.append(f"Action:      nothing — {act.refusal or 'no act'}")
    lines.append(f"Provenance:  {decision.effective_provenance.value}")
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
      - --timeout expiry (unanswered)       → exit 5, review_timed_out
        (RFC §7.2 — the same one-hour bound as the libnotify path; nothing
        runs, and the record says the review went unanswered)
      - any other exit (zenity's own error) → review_failed

    The program runs through the branded dialog's bounded runner
    (consent_dialog.run_bounded): past its bound it is taken down and verified
    ended (review_timed_out); one that cannot be verified ended is logged
    with its pid and answered review_failed, never review_timed_out.
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
        result = consent_dialog.run_bounded(
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
            timeout=FALLBACK_TIMEOUT_SECONDS + 60,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "Review dialog unanswered after %d seconds — not run (RFC §7.2)",
            FALLBACK_TIMEOUT_SECONDS + 60,
        )
        return REVIEW_TIMED_OUT
    except consent_dialog.DialogNotTakenDown:
        # Not verified closed (logged with its pid): not a deadline that
        # closed it, so the record is a failed review.
        return REVIEW_FAILED
    except OSError as e:
        logger.error("zenity invocation failed: %s — not run", e)
        return REVIEW_FAILED
    if result.returncode == 5:
        logger.warning(
            "Review dialog unanswered after %d seconds — not run (RFC §7.2)",
            FALLBACK_TIMEOUT_SECONDS,
        )
        return REVIEW_TIMED_OUT
    if result.returncode == 0:
        return "allow_once"
    if result.returncode != 1:
        # zenity's own error exit: nobody answered the dialog.
        logger.error("zenity exited %d without an answer — not run",
                     result.returncode)
        return REVIEW_FAILED
    stdout = result.stdout.strip()
    if "Allow for this conversation" in stdout:
        return "allow_conversation"
    # Exit 1 with no extra-button text: the person pressed Deny, pressed Esc,
    # or closed the dialog.
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
    timeout elapses nothing runs and the answer is review_timed_out (RFC §7.2
    one-hour bound). If notify-send itself is unavailable or fails, we cannot
    even surface the held action to the user, and if zenity is absent nobody
    can answer it: nothing runs and the answer is review_failed, rather than
    executing without consent or recording a Deny nobody gave.
    """
    notify_send = shutil.which("notify-send")
    if notify_send is None:
        logger.warning(
            "notify-send not found — not run, review failed (RFC §7.2: "
            "no surface to inform the user a held action is waiting)"
        )
        return REVIEW_FAILED
    # The fallback's whole purpose is to wait out a LOCKED/INACTIVE session and
    # re-prompt zenity once it returns. If zenity is not installed at all, that
    # re-prompt can never succeed — looping on it every poll for the full
    # FALLBACK_TIMEOUT_SECONDS (one hour) is a pure wedge: a CLI/headless `ask`
    # hangs the entire window for nothing. Detect the absent-zenity case once,
    # surface the held action via the notification, and answer promptly that the
    # review failed (no interactive GUI consent path exists, so waiting buys
    # nothing).
    zenity_available = shutil.which("zenity") is not None
    summary = "InterGen — action review needed"
    body = _format_modal_body(
        call, decision, source_attribution, excerpt, reasoning
    )
    try:
        consent_dialog.run_bounded(
            [
                notify_send, "--app-name=intergen",
                "--urgency=critical",
                "--expire-time=0",
                summary, body,
            ],
            timeout=5, capture_output=False, what="notify-send",
            consequence="it may still be running; not run",
        )
    except (subprocess.TimeoutExpired, OSError,
            consent_dialog.DialogNotTakenDown) as e:
        logger.error("notify-send failed: %s — not run, review failed", e)
        return REVIEW_FAILED
    if not zenity_available:
        logger.warning(
            "zenity not installed — no interactive GUI consent path for held "
            "action %s; notified the user, not run, review failed (not looping "
            "for %ds). Install zenity to enable the approval modal.",
            call.name, FALLBACK_TIMEOUT_SECONDS,
        )
        return REVIEW_FAILED
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
        "Review for %s expired after %ds — not run (RFC §7.2)",
        call.name, FALLBACK_TIMEOUT_SECONDS,
    )
    return REVIEW_TIMED_OUT


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
        # deny until the review times out after 1h). On a normal desktop boot this must NOT
        # happen: _ensure_display_env() should have recovered the session at review
        # time. Log it LOUDLY (requirement 2026-06-28 — degraded consent/
        # teaching states must be impossible to miss); a DEFECT to root-cause, not rest.
        logger.warning(
            "consent: NO active session — routing the held action to the buttonless "
            "libnotify fallback (D-008 dead-end; not run if unanswered in 1h). On a normal "
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
