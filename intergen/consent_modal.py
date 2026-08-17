# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Phone-a-Friend consent modal — show-before-send (Sentinel design plan §4).

The consent surface for the consent-first escalation: BEFORE any conversation
content leaves the machine for the user's configured frontier model, show the
user the EXACT outbound content + the destination provider and require an explicit
Send. This is the show-before-send seam the security review requires: the genuine
initial human-authorized hop is NOT egress-scanned (decision #6), so its entire
safety rests on the human actually SEEING what is about to be sent — including any
secret already sitting in the conversation — before they authorize it.

Why a standalone helper and not review_modal.prompt_review: that surface is coupled
to (ToolCall, DispatchDecision) — the AI-6 dispatch-review domain. Phone-a-friend
consent is a different decision (an egress payload + a provider, Send/Cancel), so it
gets its own modal but REUSES review_modal's proven session-detect + zenity-primary +
libnotify-degrade discipline.

Fail-closed: anything other than an explicit Send is a Cancel (deny). zenity
unavailable / session inactive / notify-send unavailable / any error -> Cancel. An
egress to a third party must never default to send.
"""

from __future__ import annotations

import logging
import shutil
import subprocess

# Reuse review_modal's session-active probe so the two consent surfaces behave
# identically about when a GUI modal is reachable vs the console path.
from intergen import consent_dialog, eval_consent
from intergen.review_modal import _session_active

logger = logging.getLogger(__name__)

def _format_body(content: str, provider: str, reason: str) -> str:
    """Render the consent dialog body: destination + reason + the VERBATIM payload.

    show-before-send completeness (security review note #2): the body contains the
    FULL outbound content, never truncated. The consented hop is not egress-scanned,
    so this dialog is the ONLY thing between a secret and the network — it must show
    100% of what can leave. The scrollable --text-info dialog (not --question, whose
    text gets unwieldy and was previously display-capped below the send size) keeps an
    arbitrarily long payload fully reviewable, so SHOWN always equals SENT.
    """
    lines = [
        f"InterGen wants to send the content below to your configured frontier "
        f"model ({provider}).",
        "",
        "This leaves your machine. Review ALL of it before allowing it —",
        "including anything sensitive already in the conversation.",
    ]
    if reason:
        lines += [f"Why: {reason}"]
    lines += ["", "──────── Outbound content (exactly what will be sent) ────────", "",
              content]
    return "\n".join(lines)


def _prompt_consent_zenity(content: str, provider: str, reason: str) -> bool | None:
    """Synchronous zenity --text-info modal (scrollable, full payload). Returns
    True (Send) / False (Cancel), or None if zenity is unavailable so the caller
    can route to the fallback.

    Button mapping: --ok-label "Send" -> rc 0 (True); --cancel-label "Cancel" /
    Esc / window-close -> rc != 0 (False). Default + safe action is Cancel.
    """
    zenity = shutil.which("zenity")
    if zenity is None:
        logger.warning("zenity not found — routing phone-a-friend consent to fallback")
        return None
    body = _format_body(content, provider, reason)
    try:
        # --text-info renders a SCROLLABLE view of the full body fed on stdin, so the
        # entire outbound payload is reviewable regardless of length (note #2: SHOWN ==
        # SENT). Send/Cancel via ok/cancel labels; --default-cancel keeps it fail-closed.
        result = subprocess.run(
            [
                zenity, "--text-info",
                "--title=InterGen — send to your frontier model?",
                "--width=760", "--height=520",
                "--ok-label=Send",
                "--cancel-label=Cancel",
                "--default-cancel",
            ],
            input=body, capture_output=True, text=True,
        )
    except OSError as e:
        logger.error("zenity invocation failed: %s — consent denied (no send)", e)
        return False
    return result.returncode == 0


def _prompt_consent_libnotify(provider: str) -> bool:
    """Headless / no-zenity fallback. We cannot render a full reviewable payload
    in a notification, and show-before-send REQUIRES the user actually see the
    content — so the fallback fails CLOSED (no send) and tells the user to retry
    in an active graphical session. A best-effort notification explains why.

    Returns False always (deny): an unattended egress to a third party must never
    proceed without the human having seen the content.
    """
    notify_send = shutil.which("notify-send")
    if notify_send is not None:
        try:
            subprocess.run(
                [
                    notify_send, "--urgency=critical",
                    "InterGen — frontier-model send blocked",
                    (f"A phone-a-friend send to {provider} needs your review, but no "
                     "graphical prompt is available. Nothing was sent. Retry in an "
                     "active desktop session so you can review the content first."),
                ],
                capture_output=True, check=False,
            )
        except OSError as e:
            logger.error("notify-send failed: %s", e)
    logger.warning("phone-a-friend consent unavailable (no session/zenity); "
                   "denied — show-before-send cannot be honored headless")
    return False


def prompt_send_consent(content: str, provider: str, reason: str = "") -> bool:
    """Show-before-send consent gate. Return True only on an explicit human Send.

    Routes to the branded GTK send-confirm dialog when the desktop session is
    active (zenity as fallback), else the fail-closed libnotify path. Fail-closed
    everywhere: the only path to True is the user clicking Send on a dialog that
    showed them the full outbound content.
    """
    # Eval-mode deny-and-record. UNARMED in production, where this guard is False
    # and the function continues into the identical code path below — so shipped
    # consent behavior is unchanged. When an unattended baseline run has armed the
    # responder, the send is refused immediately and recorded, rather than raising
    # a dialog no one is present to answer. The responder can only ever return
    # False here, so this branch cannot authorize an egress.
    if eval_consent.is_armed():
        return eval_consent.send_verdict(content, provider, reason)
    if _session_active():
        # Log the path BEFORE the blocking modal (same SSH-observable proof signal
        # as review_modal — invariant #7): the dialog blocks on a click, so an
        # after-the-fact log would never fire without interaction.
        logger.info(
            "consent: session active — rendering the branded GTK send-confirm "
            "dialog (zenity fallback ready)")
        gtk_result = consent_dialog.run_consent_dialog(content, provider, reason)
        if gtk_result is not None:
            return gtk_result
        logger.warning(
            "consent: branded GTK send-confirm did not render — falling back to "
            "the zenity show-before-send modal")
        result = _prompt_consent_zenity(content, provider, reason)
        if result is not None:
            return result
    else:
        logger.warning(
            "consent: NO active session — phone-a-friend send blocked "
            "(show-before-send cannot be honored headless; nothing sent)")
    return _prompt_consent_libnotify(provider)
