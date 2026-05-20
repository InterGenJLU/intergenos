"""Dispatcher gate for InterGen tool calls.

Implementation of D-008 RFC v1.0 minimum scope, including the D-008
amendment 2026-05-19T21:47:58Z that pulled spotlighting + per-conversation
trust state from v1.x into v1.0.

RFC: docs/architecture/intergen-provenance-gate-design.md

This module is the dispatcher gate proper — it sits between the LLM's
tool-call proposal and tool_registry.execute(). Inputs:

  - the proposed ToolCall (carrying declared source_of_request per §5.3)
  - the per-turn IngressTracker (tracks which ingress tools have fired
    earlier in the current conversation turn — RFC §5.1)
  - the ConversationTrustState (records prior allow/deny-conversation
    decisions for the current conversation — D-008 amendment)
  - the tool's risk tier (orthogonal to provenance per RFC §6)
  - an optional source attribution for the ingress content that
    motivated the call (surfaced in review modal)

Output: a DispatchDecision describing what to do (execute / hold / reject)
plus the effective provenance label after watermark escalation, the
pkexec-requirement flag for downstream D-007 Option A integration, and
a human-readable reason string for the audit log + review modal.

The dispatcher is consulted twice in the normal flow:

  1. BEFORE tool execution: verify_tool_call() returns a DispatchDecision.
     If decision.action == "execute", proceed. If "hold_for_review",
     the caller (tool_registry) routes to the review modal. If "reject",
     the call is refused (audit-logged) and a tool error returned to the
     LLM.

  2. AFTER user review (when held): record_user_decision() updates the
     ConversationTrustState if the user picked allow-conversation or
     deny-conversation; otherwise (allow-once / deny-one-time) trust
     state is untouched.

Composes with:
  - D-007 Option A pkexec gate (downstream; verify_tool_call sets
    needs_pkexec for the PRIVILEGED_STATE_CHANGING tier)
  - audit_log module (after dispatch + user review, an AuditRecord is
    written; this module exposes build_audit_record_for_decision to
    keep the construction in one place)
  - spotlighting module (orthogonal; ingress tools wrap their own
    outputs which influence the LLM's source_of_request declaration)
"""

from __future__ import annotations

import logging
from typing import Iterable

from intergen.interfaces.provenance import (
    AuditRecord,
    ConversationTrustState,
    DispatchDecision,
    IngressTracker,
    Provenance,
    ToolRiskTier,
    UserDecision,
    escalate_provenance,
    utc_iso_now,
)
from intergen.interfaces.types import ToolCall

logger = logging.getLogger(__name__)


# Behavior matrix per RFC §6.
# Maps (tool_risk_tier, effective_provenance) -> ("execute" | "hold_for_review").
# pkexec requirement is a SEPARATE axis — it fires whenever the tool tier
# is PRIVILEGED_STATE_CHANGING, regardless of provenance.
_BEHAVIOR_MATRIX: dict[
    tuple[ToolRiskTier, Provenance], str
] = {
    # READ_ONLY: execute for all provenance levels (read-only tools cannot
    # state-change; the gate's job is intent verification for actions, not
    # information access). Read-only calls under ingress_derived still get
    # audit-logged.
    (ToolRiskTier.READ_ONLY, Provenance.USER_DIRECT): "execute",
    (ToolRiskTier.READ_ONLY, Provenance.USER_IMPLIED): "execute",
    (ToolRiskTier.READ_ONLY, Provenance.INGRESS_DERIVED): "execute",
    # USER_SCOPE_STATE_CHANGING: execute for user_direct/implied; hold for
    # review on ingress_derived (the user did not ask for this state-change
    # in their own namespace; surface for explicit consent).
    (ToolRiskTier.USER_SCOPE_STATE_CHANGING, Provenance.USER_DIRECT): "execute",
    (ToolRiskTier.USER_SCOPE_STATE_CHANGING, Provenance.USER_IMPLIED): "execute",
    (ToolRiskTier.USER_SCOPE_STATE_CHANGING, Provenance.INGRESS_DERIVED): "hold_for_review",
    # PRIVILEGED_STATE_CHANGING: execute (+pkexec) only on user_direct;
    # hold for review on user_implied AND ingress_derived. pkexec STILL
    # fires after user allows the held action — provenance gate authorizes
    # intent; pkexec authorizes authentication.
    (ToolRiskTier.PRIVILEGED_STATE_CHANGING, Provenance.USER_DIRECT): "execute",
    (ToolRiskTier.PRIVILEGED_STATE_CHANGING, Provenance.USER_IMPLIED): "hold_for_review",
    (ToolRiskTier.PRIVILEGED_STATE_CHANGING, Provenance.INGRESS_DERIVED): "hold_for_review",
}


def _build_pair_key(call: ToolCall, source_attribution: str) -> tuple[str, str]:
    """Key into ConversationTrustState. (tool_name, source) — source is
    the attribution string surfaced in the review modal; same string used
    to remember user's allow/deny-conversation decision so the next call
    of the same (tool, source) pair gets the prior decision without a
    re-prompt.
    """
    return (call.name, source_attribution)


def verify_tool_call(
    call: ToolCall,
    ingress_tracker: IngressTracker,
    trust_state: ConversationTrustState,
    tool_risk_tier: ToolRiskTier,
    source_attribution: str = "",
) -> DispatchDecision:
    """Apply the dispatcher gate per RFC §4 + §5.1 + §6.

    Args:
        call: the LLM's proposed ToolCall (must have non-None
            source_of_request; ToolCall.__post_init__ enforces this).
        ingress_tracker: per-turn ingress-tool-fire tracker. Must reflect
            tools fired BEFORE the call under review in the current turn
            (the call under review must NOT be self-recorded — caller
            records after dispatch decision).
        trust_state: per-conversation symmetric allow/deny memory.
        tool_risk_tier: the registered tool's risk tier from the tool's
            BaseTool.classify_safety or equivalent.
        source_attribution: optional short label for the ingress source
            that motivated this call (URL / file path / etc.). Empty
            string if the call is user-direct + no ingress involved.

    Returns:
        DispatchDecision with action ∈ {"execute","hold_for_review","reject"},
        effective_provenance after watermark escalation, needs_pkexec flag,
        reason string, and trust_state_consulted flag.
    """
    # Defensive: __post_init__ on ToolCall enforces non-None already, but a
    # mutable assignment after construction could violate the invariant.
    # Re-check at the gate to maintain the no-fallback guarantee end-to-end
    # per RFC §5.3.
    if call.source_of_request is None:
        return DispatchDecision(
            action="reject",
            effective_provenance=Provenance.INGRESS_DERIVED,
            needs_pkexec=False,
            reason=(
                "RFC §5.3 violation: ToolCall has no source_of_request declared. "
                f"Tool: {call.name!r}. The LLM system-prompt (§8) requires the "
                "provenance label on every tool call."
            ),
            trust_state_consulted=False,
        )

    declared = call.source_of_request
    effective = escalate_provenance(
        declared,
        ingress_tracker.ingress_fired_this_turn(),
    )

    needs_pkexec = tool_risk_tier == ToolRiskTier.PRIVILEGED_STATE_CHANGING

    # ConversationTrustState check — if the user previously made an
    # allow-conversation or deny-conversation decision for this exact
    # (tool, source) pair within the current conversation, honor it.
    # Symmetric per Q7 (SPOC concur 2026-05-19T22:12:16Z): both allow
    # AND deny persist within the conversation.
    pair_key = _build_pair_key(call, source_attribution)
    prior = trust_state.check(*pair_key)
    if prior == "allow":
        return DispatchDecision(
            action="execute",
            effective_provenance=effective,
            needs_pkexec=needs_pkexec,
            reason=(
                f"Prior allow-conversation decision for ({call.name}, "
                f"{source_attribution}); honoring within this conversation."
            ),
            trust_state_consulted=True,
        )
    if prior == "deny":
        return DispatchDecision(
            action="reject",
            effective_provenance=effective,
            needs_pkexec=needs_pkexec,
            reason=(
                f"Prior deny-conversation decision for ({call.name}, "
                f"{source_attribution}); refusing within this conversation."
            ),
            trust_state_consulted=True,
        )

    # No prior decision: apply the RFC §6 behavior matrix.
    matrix_action = _BEHAVIOR_MATRIX.get(
        (tool_risk_tier, effective),
        "hold_for_review",  # safe default if a new tier/provenance combo is added
    )

    if matrix_action == "execute":
        reason = (
            f"Behavior matrix: {tool_risk_tier.value} + {effective.value} = execute."
        )
        if effective != declared:
            reason += (
                f" (Effective provenance escalated from declared "
                f"{declared.value} to {effective.value} via ingress-tool "
                f"watermark; ingress history: "
                f"{ingress_tracker.history()})"
            )
        return DispatchDecision(
            action="execute",
            effective_provenance=effective,
            needs_pkexec=needs_pkexec,
            reason=reason,
            trust_state_consulted=False,
        )

    # matrix_action == "hold_for_review"
    reason = (
        f"Behavior matrix: {tool_risk_tier.value} + {effective.value} requires "
        "user review (action not directly requested by user)."
    )
    if effective != declared:
        reason += (
            f" (Effective provenance escalated from declared "
            f"{declared.value} to {effective.value} via ingress-tool "
            f"watermark; ingress history: "
            f"{ingress_tracker.history()})"
        )
    return DispatchDecision(
        action="hold_for_review",
        effective_provenance=effective,
        needs_pkexec=needs_pkexec,
        reason=reason,
        trust_state_consulted=False,
    )


def record_user_decision(
    user_decision: UserDecision,
    call: ToolCall,
    trust_state: ConversationTrustState,
    source_attribution: str,
) -> None:
    """Apply the user's review-modal decision to ConversationTrustState.

    Only "allow_conversation" and "deny_conversation" update the trust
    state — "allow_once" + "deny_one_time" are one-shot and do not change
    policy. Symmetric per Q7 SPOC concur.
    """
    pair_key = _build_pair_key(call, source_attribution)
    if user_decision.decision == "allow_conversation":
        trust_state.remember_decision(*pair_key, "allow")
    elif user_decision.decision == "deny_conversation":
        trust_state.remember_decision(*pair_key, "deny")
    # "allow_once" and "deny_one_time" are intentionally not persisted.


def build_audit_record(
    call: ToolCall,
    decision: DispatchDecision,
    ingress_tracker: IngressTracker,
    user_outcome: str,
    exit_code: int = 0,
    result_summary: str = "",
    source_attribution: str = "",
    excerpt: str = "",
) -> AuditRecord:
    """Construct an AuditRecord for the dispatch + user-review outcome.

    user_outcome is one of:
      "executed"             — gate said execute; tool ran
      "allowed_once"         — user reviewed and approved this single call
      "allowed_conversation" — user reviewed and approved this (tool,source)
                               pair for the conversation
      "denied"               — gate or user refused; tool did not run
    """
    truncated = result_summary[:256] if result_summary else ""
    return AuditRecord(
        timestamp=utc_iso_now(),
        tool_name=call.name,
        arguments=call.arguments,
        declared_provenance=(
            call.source_of_request.value
            if call.source_of_request is not None
            else "missing"
        ),
        effective_provenance=decision.effective_provenance.value,
        ingress_tools_this_turn=ingress_tracker.history(),
        user_decision=user_outcome,
        exit_code=exit_code,
        result_summary=truncated,
        source_attribution=source_attribution,
        excerpt=excerpt,
    )


# Re-export utility helpers callers commonly need together.
__all__: list[str] = [
    "verify_tool_call",
    "record_user_decision",
    "build_audit_record",
]
