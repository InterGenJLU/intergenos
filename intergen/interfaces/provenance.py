"""Provenance taxonomy + dispatcher-gate types per D-008 RFC v1.0.

RFC: docs/architecture/intergen-provenance-gate-design.md

This module defines the type surface for the provenance gate:
  - Provenance enum (§3) — three exhaustive categories
  - INGRESS_TOOLS_V1 (§5.1) — tools whose output may carry injection bytes
  - IngressTracker (§5.1) — per-turn ingress-tool-fire tracker
  - ConversationTrustState (§10 v1.x pulled to v1.0 per D-008 amendment) —
    symmetric allow/deny-once-conversation state
  - AuditRecord (§9) — append-only audit log entry shape
  - UserDecision — review modal outcome

Composes with intergen/interfaces/types.py ToolCall (extended with required
source_of_request field per RFC §5.3 no-fallback policy).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Provenance(Enum):
    """Per RFC §3 — exactly one label per tool call. No fallback per §5.3.

    USER_DIRECT: the action is explicitly described in the most recent user
        prompt or a direct mechanical reading of it.

    USER_IMPLIED: the action is a reasonable follow-on the user would expect
        but did not literally name.

    INGRESS_DERIVED: the action emerged from content the LLM read or fetched,
        not from the user's prompt (webpages, files, search results).
    """
    USER_DIRECT = "user_direct"
    USER_IMPLIED = "user_implied"
    INGRESS_DERIVED = "ingress_derived"


# Ingress set per RFC §5.1 v1.0 — tools whose output may carry free-text
# injection content. Read-only system-state inspection (e.g. manage_services
# in query mode) returns structured machine data and is NOT in this set;
# false-positive rate would swamp the gate.
INGRESS_TOOLS_V1: frozenset[str] = frozenset({
    "read_file",
    "read_url",
    "web_search",
    "read_clipboard",
    "list_directory",
})


class ToolRiskTier(Enum):
    """Per RFC §6 — orthogonal to provenance.

    READ_ONLY: returns information; does not modify system state.
    USER_SCOPE_STATE_CHANGING: modifies state in user's own namespace; no
        privilege escalation.
    PRIVILEGED_STATE_CHANGING: requires escalation (downstream pkexec gate
        per D-007 Option A).
    """
    READ_ONLY = "read_only"
    USER_SCOPE_STATE_CHANGING = "user_scope_state_changing"
    PRIVILEGED_STATE_CHANGING = "privileged_state_changing"


@dataclass
class UserDecision:
    """The user's decision on a held tool call (review modal outcome)."""
    decision: str  # "allow_once" | "allow_conversation" | "deny"
    timestamp: datetime
    note: str = ""

    @staticmethod
    def now_utc(decision: str, note: str = "") -> "UserDecision":
        return UserDecision(
            decision=decision,
            timestamp=datetime.now(timezone.utc),
            note=note,
        )


@dataclass
class DispatchDecision:
    """Output of the dispatcher gate per RFC §4 + §6.

    action is one of:
      - "execute"          — proceed to tool execution (still subject to
                             downstream pkexec for PRIVILEGED tier)
      - "hold_for_review"  — pause for user review modal (allow/deny/explain)
      - "reject"           — refuse the call (schema violation; missing
                             source_of_request; trust-state denial)

    effective_provenance is the post-watermark-escalation provenance label.

    needs_pkexec is True when the tool risk tier is PRIVILEGED_STATE_CHANGING;
    the dispatcher composes pkexec downstream of this gate per RFC §4
    flowchart "Execute (still respect pkexec for priv.)".

    reason is human-readable; used in audit log + surfaced in review modal.
    """
    action: str
    effective_provenance: "Provenance"
    needs_pkexec: bool
    reason: str = ""
    trust_state_consulted: bool = False  # True if ConversationTrustState had a prior decision


@dataclass
class AuditRecord:
    """Append-only audit log entry per RFC §9.

    Written one-per-line as JSON to
    $XDG_STATE_HOME/intergen/tool-dispatch.jsonl
    Reviewed by `intergen tool-log` CLI subcommand.
    """
    timestamp: str  # ISO8601 UTC
    tool_name: str
    arguments: dict[str, Any]
    declared_provenance: str  # Provenance.value
    effective_provenance: str  # Provenance.value after ingress-watermark escalation
    ingress_tools_this_turn: list[str]
    user_decision: str  # "executed" | "allowed_once" | "allowed_conversation" | "denied"
    exit_code: int = 0
    result_summary: str = ""  # truncated to first 256 chars
    source_attribution: str = ""  # e.g. "https://example.com" or "/etc/fstab"
    excerpt: str = ""  # the snippet of ingress content that prompted the action (if any)

    def to_jsonl_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for json.dumps + newline."""
        return {
            "timestamp": self.timestamp,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "declared_provenance": self.declared_provenance,
            "effective_provenance": self.effective_provenance,
            "ingress_tools_this_turn": self.ingress_tools_this_turn,
            "user_decision": self.user_decision,
            "exit_code": self.exit_code,
            "result_summary": self.result_summary,
            "source_attribution": self.source_attribution,
            "excerpt": self.excerpt,
        }


class IngressTracker:
    """Per-turn ingress-tool-fire tracker per RFC §5.1.

    A new instance is created at each conversation-turn boundary in router.py.
    Tracks which ingress tools fired *before* the action under review in the
    current turn. The dispatcher consults this when escalating effective
    provenance via the watermark heuristic.
    """

    def __init__(self, ingress_set: frozenset[str] = INGRESS_TOOLS_V1) -> None:
        self._ingress_set = ingress_set
        self._fired_this_turn: list[str] = []

    def record_tool_call(self, tool_name: str) -> None:
        """Record that a tool fired this turn. Call AFTER dispatch decision
        (so the action under review sees only PRIOR tool fires, not its own).
        """
        if tool_name in self._ingress_set:
            self._fired_this_turn.append(tool_name)

    def ingress_fired_this_turn(self) -> bool:
        return bool(self._fired_this_turn)

    def history(self) -> list[str]:
        """Return a copy of the per-turn ingress-fire log (for audit records)."""
        return list(self._fired_this_turn)

    def reset(self) -> None:
        """Call at conversation-turn boundary in router.py."""
        self._fired_this_turn.clear()


class ConversationTrustState:
    """Per-conversation symmetric allow/deny-once-conversation state.

    RFC §10 v1.x item 3 pulled into v1.0 per D-008 amendment 2026-05-19T21:47:58Z.

    Records user decisions (allow-once-conversation OR deny-once-conversation)
    keyed by (tool_name, source_attribution). Symmetric: both allow and deny
    persist within the conversation; reset at conversation end via .reset()
    called by router.py at conversation-end boundary.
    """

    _VALID_DECISIONS = ("allow", "deny")

    def __init__(self) -> None:
        # (tool_name, source) -> "allow" | "deny"
        self._decisions: dict[tuple[str, str], str] = {}

    def remember_decision(
        self,
        tool_name: str,
        source: str,
        decision: str,
    ) -> None:
        """Record user's once-conversation decision.

        decision must be 'allow' or 'deny' (the once-conversation choices;
        'allow_once' decisions are NOT recorded here — those are one-shot).
        """
        if decision not in self._VALID_DECISIONS:
            raise ValueError(
                f"decision must be one of {self._VALID_DECISIONS}, got: {decision!r}"
            )
        self._decisions[(tool_name, source)] = decision

    def check(self, tool_name: str, source: str) -> str | None:
        """Return prior decision for this (tool, source) pair, or None.

        Used by the dispatcher to skip the review modal when the user has
        already made an allow/deny-conversation decision for this exact pair
        in the current conversation.
        """
        return self._decisions.get((tool_name, source))

    def reset(self) -> None:
        """Call at conversation-end boundary."""
        self._decisions.clear()


def escalate_provenance(
    declared: Provenance,
    ingress_fired_this_turn: bool,
) -> Provenance:
    """Apply the ingress-tool-watermark heuristic per RFC §5.1 table.

    | Declared label | Ingress fired? | Effective label |
    |---|---|---|
    | USER_DIRECT    | no  | USER_DIRECT     |
    | USER_DIRECT    | yes | USER_IMPLIED    |
    | USER_IMPLIED   | no  | USER_IMPLIED    |
    | USER_IMPLIED   | yes | INGRESS_DERIVED |
    | INGRESS_DERIVED | n/a | INGRESS_DERIVED |
    """
    if not ingress_fired_this_turn:
        return declared
    if declared == Provenance.USER_DIRECT:
        return Provenance.USER_IMPLIED
    if declared == Provenance.USER_IMPLIED:
        return Provenance.INGRESS_DERIVED
    return Provenance.INGRESS_DERIVED  # already top tier


def utc_iso_now() -> str:
    """ISO8601 UTC timestamp for audit records."""
    return datetime.now(timezone.utc).isoformat()
