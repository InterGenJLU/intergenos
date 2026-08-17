# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Eval-mode consent responder — deny-and-record for unattended baselines.

Unattended per-tier scenario baselines drive the live daemon over D-Bus with a
corpus whose locked-posture scenarios provoke real system-action attempts. Those
attempts correctly raise InterGen's consent gates. The gate firing is CORRECT and
is itself measurement data; what the batch harness lacked was a non-interactive
answer, so a run stalled on a modal nobody was there to click (and a held modal
starved an in-flight ResetConversation).

The ratified policy is DENY-AND-RECORD, implemented harness-side:

  * every consent gate a scenario can fire resolves to an IMMEDIATE deny;
  * the deny is RECORDED — gate type, requested action summary, turn correlation
    — so grading can see that (and when) InterGen sought consent;
  * arming eval mode is itself a loud event, never silent.

Auto-approve and blind modal dismissal are both ruled out: the first widens what
an unattended run can do to the box, the second destroys the measurement.

WHY THIS IS SAFE TO EXIST AT ALL — the monotonicity argument
------------------------------------------------------------
This responder can only ever answer DENY. There is no allow path, no mode
parameter, and no verdict input: :func:`review_verdict` returns the literal
``"deny"`` and :func:`send_verdict` returns the literal ``False``. Arming it is
therefore monotonically RESTRICTIVE — it cannot grant a capability, cannot
approve a privileged dispatch, and cannot authorize an egress. The worst outcome
of an unintended arming is that actions the user wanted are refused, loudly and
on the record, which is the safe direction of failure.

That asymmetry is the whole reason an arming channel is tolerable here, and it is
why this module deliberately does NOT reuse or extend the launch-time review
autopilot in dbus_daemon.py: that surface has an ``allow`` mode, so its
constraints are not this module's constraints.

ARMING CONTRACT (hard requirements — do not relax without re-deciding)
---------------------------------------------------------------------
* **Production behavior is unchanged when unarmed.** Every seam is a single
  guard that is False in production, falling through to the exact prior code
  path. The destructive never-list is untouched and gains no config surface.
* **No persistent arming channel.** Armed state lives in this module's process
  memory only. There is no config file, no environment variable, and no D-Bus
  setter — a live daemon's consent posture cannot be flipped over the bus, which
  is a standing invariant of the daemon's consent design and is preserved here
  verbatim. Arming happens at daemon CONSTRUCTION, from the explicit
  ``--eval-consent-deny`` argument on that daemon process's own command line.
* **Fail-closed.** An absent, malformed, or ambiguous marker leaves the
  responder DISARMED, which means production behavior (real gates). The refusal
  is logged and emitted to glass; it is never a silent no-op.
* **The fallback wedge is never entered.** review_modal's one-hour
  implicit-deny fallback exists for a human who may return to the machine. An
  armed run answers before that path is reached, so a batch run cannot hang on
  it.

WHAT ARMING DOES NOT DO
-----------------------
It answers the consent QUESTION only. It does not widen what is dispatchable:
the command safety denylist, the destructive never-list, and the dispatch-policy
lockdown all run independently and are untouched.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from intergen import glass

logger = logging.getLogger(__name__)

# The explicit arming marker. A caller must supply this exact token; anything
# else (empty, misspelled, wrong version, non-string) is malformed and leaves the
# responder disarmed. Versioned so a future policy change cannot be armed by a
# stale invocation that predates it.
ARM_MARKER = "eval-consent-deny-v1"

# Gate identifiers recorded on every observation. These are the two consent
# surfaces a scenario-driven turn can reach; see the reachability notes in the
# branch's test module.
GATE_ACTION_REVIEW = "action_review"
GATE_PHONE_A_FRIEND_SEND = "phone_a_friend_send"

# The only verdicts this module can produce. Named so a reader does not have to
# infer the deny-only property from control flow.
REVIEW_VERDICT_DENY = "deny"
SEND_VERDICT_DENY = False

# Cap on retained in-process observations. The authoritative record is the glass
# row emitted per denial (which live_run joins by turn id); this list is a
# convenience for a direct/in-process harness reading the responder straight.
# Bounded so a long run cannot grow it without limit.
_MAX_OBSERVATIONS = 10000

_lock = threading.Lock()
_armed = False
_observations: list[dict[str, Any]] = []
_truncated = 0


def _summarize(value: Any, limit: int = 300) -> str:
    """Render an argument/payload as a bounded single-line summary.

    The observation is measurement metadata, not a content archive — the full
    payload already lives in the turn's own trace. Bounding it keeps a large
    tool argument or outbound body from bloating every recorded denial.
    """
    try:
        text = value if isinstance(value, str) else repr(value)
    except Exception:  # noqa: BLE001 — a broken __repr__ must not break recording
        return "<unrenderable>"
    text = " ".join(text.split())
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def arm(marker: str) -> bool:
    """Arm the deny-and-record responder. Returns True iff now armed.

    Fail-closed: any marker that is not exactly :data:`ARM_MARKER` leaves the
    responder disarmed and logs the refusal. Arming is loud — a warning on the
    logger and an ``eval_consent/armed`` glass row — because a non-interactive
    consent posture must never be silent.
    """
    global _armed
    if not isinstance(marker, str) or marker != ARM_MARKER:
        logger.warning(
            "eval-consent arming REFUSED — marker absent or malformed (%s); "
            "the daemon keeps PRODUCTION consent behavior (real gates)",
            _summarize(marker, 60),
        )
        _emit("arm_refused", {"reason": "malformed_marker"})
        return False
    with _lock:
        _armed = True
    logger.warning(
        "EVAL CONSENT RESPONDER ARMED (deny-and-record) — every consent gate "
        "resolves to an immediate DENY and is recorded. This answers the consent "
        "question only; the safety denylist and dispatch lockdown are untouched. "
        "NEVER launch a production daemon with --eval-consent-deny.",
    )
    _emit("armed", {"marker": ARM_MARKER, "policy": "deny_and_record"})
    return True


def disarm() -> None:
    """Return to production consent behavior and clear recorded observations.

    Used by tests and by a harness tearing down its own in-process daemon. The
    disarm is emitted too, so the armed window has both edges on the record.
    """
    global _armed, _truncated
    with _lock:
        was_armed = _armed
        _armed = False
        _observations.clear()
        _truncated = 0
    if was_armed:
        logger.warning("eval-consent responder DISARMED — production consent "
                       "behavior restored")
        _emit("disarmed", {})


def is_armed() -> bool:
    """True iff the deny-and-record responder is armed in this process."""
    with _lock:
        return _armed


def observations() -> list[dict[str, Any]]:
    """A copy of the recorded consent observations, oldest first."""
    with _lock:
        return list(_observations)


def observation_summary() -> dict[str, Any]:
    """Compact roll-up for status surfaces and end-of-run reporting."""
    with _lock:
        rows = list(_observations)
        truncated = _truncated
        armed = _armed
    per_gate: dict[str, int] = {}
    for row in rows:
        per_gate[row["gate"]] = per_gate.get(row["gate"], 0) + 1
    return {
        "armed": armed,
        "policy": "deny_and_record" if armed else None,
        "denials": len(rows) + truncated,
        "recorded": len(rows),
        "truncated": truncated,
        "per_gate": per_gate,
    }


def _emit(event: str, detail: dict[str, Any]) -> None:
    """Emit one glass row, never raising into the consent path.

    Recording must not be able to break a dispatch: a glass failure degrades to a
    debug line, exactly as the daemon's own emission sites do.
    """
    try:
        glass.emit("consent", f"eval_{event}", detail=detail)
    except Exception as e:  # noqa: BLE001 — recording must never break consent
        logger.debug("eval-consent glass emit failed (%s): %s", event, e)


def _record(gate: str, action: str, extra: dict[str, Any]) -> dict[str, Any]:
    """Record one denied consent as an observation + a glass row.

    The glass row is the authoritative artifact: it carries the active turn id,
    which is what joins the denial back to the scenario turn that provoked it.
    """
    global _truncated
    try:
        turn_id = glass.current_turn_id()
    except Exception:  # noqa: BLE001
        turn_id = ""
    row: dict[str, Any] = {
        "gate": gate,
        "verdict": "deny",
        "action": action,
        "turn_id": turn_id,
        "wall_time": time.time(),
    }
    row.update(extra)
    with _lock:
        if len(_observations) < _MAX_OBSERVATIONS:
            _observations.append(row)
        else:
            _truncated += 1
    logger.warning(
        "eval-consent: DENIED %s gate for %s (recorded; turn=%s)",
        gate, action, turn_id or "?",
    )
    _emit("denied", row)
    return row


def review_verdict(call: Any, decision: Any) -> str:
    """Answer the action-review gate: an immediate, recorded deny.

    Signature matches tool_registry.execute()'s ``review_callback`` contract
    (``(call, decision) -> str``). Returns the literal deny verdict with no
    dialog, no polling, and no path into review_modal's fallback wait — a batch
    run cannot wedge here.
    """
    name = getattr(call, "name", "?")
    args = getattr(call, "arguments", None)
    _record(
        GATE_ACTION_REVIEW,
        _summarize(name, 80),
        {
            "arguments": _summarize(args),
            "provenance": _summarize(
                getattr(getattr(decision, "effective_provenance", None), "value",
                        ""), 40),
            "needs_pkexec": bool(getattr(decision, "needs_pkexec", True)),
            "gate_reason": _summarize(getattr(decision, "reason", ""), 200),
        },
    )
    return REVIEW_VERDICT_DENY


def send_verdict(content: Any, provider: Any, reason: Any = "") -> bool:
    """Answer the phone-a-friend send gate: an immediate, recorded refusal.

    Signature matches consent_modal.prompt_send_consent's return contract
    (True only on an explicit human Send). Returns False, so nothing leaves the
    machine, and records the attempt with the destination provider. The outbound
    body is summarized rather than copied verbatim: a recorded denial must not
    become a second place a would-be-egressed secret is written down.
    """
    _record(
        GATE_PHONE_A_FRIEND_SEND,
        f"send->{_summarize(provider, 60)}",
        {
            "provider": _summarize(provider, 60),
            "reason": _summarize(reason, 200),
            "content_chars": len(content) if isinstance(content, str) else -1,
        },
    )
    return SEND_VERDICT_DENY


def make_review_callback() -> Callable[[Any, Any], str]:
    """Build the ``(call, decision) -> str`` closure the registry expects.

    Mirrors review_modal.make_review_callback's shape so the daemon can swap one
    for the other at the single call site without any other change.
    """
    def _callback(call: Any, decision: Any) -> str:
        return review_verdict(call, decision)
    return _callback
