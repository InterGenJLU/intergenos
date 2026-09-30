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

Fail-closed: anything other than an explicit Send is denied. zenity
unavailable / session inactive / notify-send unavailable / any error -> deny.
An egress to a third party must never default to send.
"""

from __future__ import annotations

import logging
import shutil
import subprocess

# Reuse review_modal's session-active probe so the two consent surfaces behave
# identically about when a GUI modal is reachable vs the console path.
from intergen import consent_dialog, consent_dialog_proto, eval_consent
from intergen.review_modal import _session_active

logger = logging.getLogger(__name__)

# WHAT THE CONSENT STEP ESTABLISHED, for a caller that passes ``outcome=[]``.
# A send that did not happen has two different causes, and the reply must say
# which: the person saw the content and did not send it, or the content could
# not be shown to them at all (no unlocked desktop session, or no dialog could
# open) and so nobody was asked. Until 2026-09-22 both came back as the same
# False and both callers told the person "Cancelled", which records a refusal
# the product made on its own behalf as the person's decision.
SEND = "send"
DECLINED = "declined"
NOT_SHOWN = "not-shown"
TOO_LARGE = "too-large"

DECLINED_SENTENCE = "Cancelled — nothing was sent to the frontier model."
NOT_SHOWN_SENTENCE = (
    "Not sent — there was no way to show you the content for review first "
    "(no unlocked desktop session, or no dialog could open), so nothing was "
    "sent to the frontier model.")
TOO_LARGE_SENTENCE = (
    "Not sent — the content is too large to show you in full for review first "
    f"(over {consent_dialog_proto.MAX_PAYLOAD_BYTES:,} bytes), so nothing was "
    "sent to the frontier model.")

# What GTK writes to standard error when zenity cannot open a display: GTK 4
# and GTK 3 wording. GTK then exits 1, the status a Cancel also gives.
_DISPLAY_FAILURE_TEXTS = ("Failed to open display", "cannot open display")


def refusal_sentence(outcome: list) -> str:
    """The reply for a send the consent step did not allow.

    ``outcome`` is the list passed to :func:`prompt_send_consent`. Only a
    recorded NOT_SHOWN or TOO_LARGE says the person was never asked; anything
    else — the person's own Cancel, or a stand-in for the consent step that
    records nothing — keeps the sentence this reply has always had.
    """
    if TOO_LARGE in outcome:
        return TOO_LARGE_SENTENCE
    return NOT_SHOWN_SENTENCE if NOT_SHOWN in outcome else DECLINED_SENTENCE


def _too_large_to_show(content: str) -> bool:
    """True when no dialog may show the content: the branded dialog refuses a
    payload over its limit rather than show part of it, and show-before-send
    forbids a truncated view anywhere. Measured as the dialog measures it."""
    return (len(content.encode("utf-8", "surrogatepass"))
            > consent_dialog_proto.MAX_PAYLOAD_BYTES)


def _display_unreachable(stderr) -> bool:
    """True when zenity's standard error says it could not open a display."""
    return isinstance(stderr, str) and any(t in stderr for t in _DISPLAY_FAILURE_TEXTS)


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
    True (Send) / False (Cancel), or None if zenity is unavailable, cannot start,
    or cannot open the display, so the caller can route to the fallback.

    Button mapping: --ok-label "Send" -> rc 0 (True); --cancel-label "Cancel" /
    Esc / window-close -> rc != 0 (False). zenity 4.2.2 applies --default-cancel
    to question dialogs only, so in this text-info dialog the default response
    is its OK button, labelled Send here. A display zenity cannot open also
    exits 1; its warning on standard error tells it apart, and that case
    returns None (nothing was shown).
    """
    zenity = shutil.which("zenity")
    if zenity is None:
        logger.warning("zenity not found — routing phone-a-friend consent to fallback")
        return None
    body = _format_body(content, provider, reason)
    try:
        # --text-info renders a SCROLLABLE view of the full body fed on stdin, so the
        # entire outbound payload is reviewable regardless of length (note #2: SHOWN ==
        # SENT). Send/Cancel via ok/cancel labels. --default-cancel has no effect on a
        # text-info dialog in zenity 4.2.2 (it applies to question dialogs only).
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
        # zenity could not be started, so nothing was shown and nobody was
        # asked: that is the fallback's case (a notification, no send), not a
        # Cancel. Returning False here told the person they had cancelled.
        logger.error("zenity invocation failed: %s — no dialog could open; "
                     "routing to the fallback (no send)", e)
        return None
    if result.returncode != 0 and _display_unreachable(result.stderr):
        # GTK exits 1 when it cannot open the display, the status a Cancel
        # also gives. Nothing was shown, so nobody was asked: the fallback's
        # case, not a Cancel. Until 2026-09-22 this told the person they had
        # cancelled.
        logger.error("zenity could not open the display — no dialog could "
                     "open; routing to the fallback (no send)")
        return None
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


def prompt_send_consent(content: str, provider: str, reason: str = "", *,
                        outcome: list | None = None) -> bool:
    """Show-before-send consent gate. Return True only on an explicit human Send.

    Routes to the branded GTK send-confirm dialog when the desktop session is
    active (zenity as fallback), else the fail-closed libnotify path. Fail-closed
    everywhere: the only path to True is the user clicking Send on a dialog that
    showed them the full outbound content.

    ``outcome``, when a list is passed, receives exactly one of SEND, DECLINED,
    NOT_SHOWN or TOO_LARGE, so a caller can tell the person WHY nothing was
    sent. It is a list the caller owns rather than a new return type so that
    every existing caller, and every stand-in that replaces this function with
    a plain True or False, keeps working unchanged. NOT_SHOWN is recorded when
    no dialog showed the content: no unlocked desktop session, or neither
    dialog could open (zenity unable to open the display included). TOO_LARGE
    is recorded, before any dialog starts, when the content is over the size a
    dialog may show in full. The unattended evaluation responder's refusal is
    recorded as DECLINED — it stands in for the person, by design. One limit:
    the branded dialog reports a crash and its deadline, both after it has
    shown the content, as the same False as a Cancel; both are recorded as
    DECLINED because this function cannot tell them apart.
    """
    allowed = _ask(content, provider, reason)
    if outcome is not None:
        outcome.append(allowed)
    return allowed == SEND


def _ask(content: str, provider: str, reason: str) -> str:
    """The consent decision as SEND, DECLINED, NOT_SHOWN or TOO_LARGE."""
    # Eval-mode deny-and-record. UNARMED in production, where this guard is False
    # and the function continues into the identical code path below — so shipped
    # consent behavior is unchanged. When an unattended baseline run has armed the
    # responder, the send is refused immediately and recorded, rather than raising
    # a dialog no one is present to answer. The responder can only ever return
    # False here, so this branch cannot authorize an egress.
    if eval_consent.is_armed():
        return SEND if eval_consent.send_verdict(content, provider, reason) else DECLINED
    if _too_large_to_show(content):
        # No dialog may show part of the content, so nobody can be asked.
        # Until 2026-09-22 the branded dialog's refusal came back as the same
        # False as a Cancel and the person was told they had cancelled. No
        # desktop notification: it tells the person to retry in a desktop
        # session, which cannot help here.
        logger.warning(
            "consent: outbound payload exceeds %d bytes — not sent; no dialog "
            "may show a truncated view, so nobody was asked",
            consent_dialog_proto.MAX_PAYLOAD_BYTES)
        return TOO_LARGE
    if _session_active():
        # Log the path BEFORE the blocking modal (same SSH-observable proof signal
        # as review_modal — invariant #7): the dialog blocks on a click, so an
        # after-the-fact log would never fire without interaction.
        logger.info(
            "consent: session active — rendering the branded GTK send-confirm "
            "dialog (zenity fallback ready)")
        gtk_result = consent_dialog.run_consent_dialog(content, provider, reason)
        if gtk_result is not None:
            return SEND if gtk_result else DECLINED
        logger.warning(
            "consent: branded GTK send-confirm did not render — falling back to "
            "the zenity show-before-send modal")
        result = _prompt_consent_zenity(content, provider, reason)
        if result is not None:
            return SEND if result else DECLINED
    else:
        logger.warning(
            "consent: NO active session — phone-a-friend send blocked "
            "(show-before-send cannot be honored headless; nothing sent)")
    # Nothing showed the content, so nobody was asked. The fallback only
    # notifies and always denies.
    _prompt_consent_libnotify(provider)
    return NOT_SHOWN
