# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""InterGen Governance — Ring-0 Immutable Enforcement Layer.

ARCHITECTURE (from self-governance research, March 2026):

    Ring-0: governance.py (IMMUTABLE, hash-verified at startup)
        validates every tool call against the tier system and
        constitutional rules. The LLM NEVER touches Ring-0 code.
    Ring-1: tool_registry.execute() + provenance gate
        enforces risk tiers, ingress classification, user decisions.
    Ring-2: LLM inference (sandboxed — cannot modify Ring-0 or Ring-1).

ENFORCEMENT MODEL:
    Governance rules are PYTHON CODE, not prompts. The LLM can propose
    actions but cannot validate or authorize them. Governance checks
    run BEFORE the provenance gate — if governance blocks, the gate
    is never reached.

KEY PRINCIPLES (from research on NVIDIA OpenShell, Atlas Nomos, CORE):
    1. Deterministic gates first, LLM gates last.
    2. Fail-closed: any governance check timeout/error → DENY.
    3. Hash-verified at startup: governance code tampering → immediate
       halt of all autonomous operations.
    4. Cryptographic audit trail: append-only, agent can write but
       cannot read, modify, or delete.
    5. Cooldown/escalation: repeat actions within a window trigger
       mandatory escalation.

TIER SYSTEM (from preceding project research, adapted for InterGen):
    Tier 0 (OBSERVE): Read-only actions — read files, check system
        status, query installed packages. Auto-approved.
    Tier 1 (ADJUST): User-scoped state changes — install packages,
        enable/disable services, modify user config files. Requires
        provenance check + user approval for ingress_derived.
    Tier 2 (PROPOSE): Structural changes — modify system config,
        create/delete user accounts, change firewall rules. Requires
        user approval regardless of provenance.
    Tier 3 (ARCHITECT): Core system modifications — kernel parameters,
        bootloader config, new system services. Requires user approval
        + pkexec authentication.
    Tier 4 (OWNER): Governance changes, signing key operations,
        model file modifications. Requires physical owner access —
        cannot be approved through the web UI or CLI alone.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import Any, Callable

from intergen.private_state import private_dir, private_write_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Build-established tamper-detection baseline: the intergen package computes
# sha256 of the installed governance.py at build time and ships it read-only
# here (dm-verity-protected /usr/share). verify_hash() compares only — it never
# writes a baseline (no trust-on-first-use from the running daemon).
GOVERNANCE_HASH_PATH = Path("/usr/share/intergen/governance.sha256")
COMMANDMENTS_PATH = Path("/etc/intergen/commandments.md")  # human-readable
TIER_CONFIG_PATH = Path("/etc/intergen/governance.json")


def _resolve_tier_config_path() -> Path:
    """Where the runtime-mutable autonomy tier is persisted.

    Root (system service) keeps the canonical /etc/intergen/governance.json. A
    non-root `--user` daemon runs under ProtectSystem=strict, so /etc is
    READ-ONLY: writing the tier there raises OSError and would crash an
    owner-confirmed set_tier() (G3-15 — latent because tier changes are rare).
    Resolve to the user's writable XDG state dir instead, mirroring the daemon's
    log-path (G3-7) and memory.db (GBC002.1) per-user resolution. The tier is
    daemon-managed mutable state, not hand-edited config, so XDG_STATE_HOME is
    the correct home; load_tier() already refuses to trust a tampered/out-of-
    range value (falls back to OBSERVE), so a user-writable location does not
    weaken the owner-confirmed-elevation invariant."""
    if os.geteuid() == 0:
        return TIER_CONFIG_PATH
    state_home = Path(os.environ.get(
        "XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return state_home / "intergen" / "governance.json"

# How often the governance module re-verifies its own hash at runtime (seconds)
HASH_RECHECK_INTERVAL = 300


class AutonomyTier(IntEnum):
    """    Tiered autonomy model — from preceding project research, adapted for InterGen.

    Higher tiers = more authority. Agent starts at Tier 0 and earns
    elevation through demonstrated safe behavior over time. Only the
    owner can elevate the tier.
    """

    OBSERVE = 0     # Read-only: files, system status, package queries
    ADJUST = 1      # User-scoped changes: install packages, services, config
    PROPOSE = 2     # Structural changes: system config, users, firewall
    ARCHITECT = 3   # Core system: kernel, bootloader, new services
    OWNER = 4       # Governance changes, signing keys, model files


# Which tier is required for each tool category — tool_registry.py calls
# _classify_risk_tier() already, but governance adds a TIER constraint
# on top of the risk constraint. A tool might be risk-tier APPROVED but
# still BLOCKED by governance if the agent's autonomy tier is too low.

TIER_REQUIRED_FOR_RISK = {
    "read_only": AutonomyTier.OBSERVE,
    "user_scope_state_changing": AutonomyTier.ADJUST,
    "privileged_state_changing": AutonomyTier.ARCHITECT,
}

# Actions that ALWAYS require Tier 4 (physical owner access), regardless
# of risk classification. These cannot be approved through the web UI
# or CLI under any circumstances.
OWNER_ONLY_ACTIONS: frozenset[str] = frozenset({
    "modify_governance",
    "modify_model_files",
    "signing_key_operation",
    "modify_bootloader_chain",
    "modify_secure_boot",
})


# ---------------------------------------------------------------------------
# Governance Commandments (the constitutional rules)
# ---------------------------------------------------------------------------

COMMANDMENTS = [
    {
        "num": 1,
        "title": "Serve the system above all else.",
        "text": "Every action must serve the stability, security, and usability "
                "of the InterGenOS system and its owner. Optimization for its "
                "own sake is not permitted. Technical elegance that degrades "
                "the user experience is a failure, not a success.",
        "enforcement": "prompt_anchored",
    },
    {
        "num": 2,
        "title": "Never harm, deceive, or manipulate.",
        "text": "InterGen will never provide information it knows to be false. "
                "It will never manipulate a user's decisions or behavior. It "
                "will never take an action designed to make a user dependent. "
                "Trust, once broken, cannot be rebuilt.",
        "enforcement": "prompt_anchored",
    },
    {
        "num": 3,
        "title": "Protect privacy absolutely.",
        "text": "User conversations, personal facts, system details, and "
                "behavioral patterns are sacred. They are never transmitted "
                "externally except to fulfill an explicit user request. They "
                "are never used for purposes the owner has not approved.",
        "enforcement": "code_enforced",
    },
    {
        "num": 4,
        "title": "Fail safely, fail visibly.",
        "text": "When something goes wrong, InterGen stops and says so. It "
                "does not guess. It does not improvise a response it isn't "
                "confident in. It does not hide errors. A visible failure "
                "that gets fixed is better than a hidden failure that erodes "
                "trust.",
        "enforcement": "code_enforced",
    },
    {
        "num": 5,
        "title": "Respect the boundaries of authority.",
        "text": "InterGen operates within its assigned autonomy tier at all "
                "times. Tier boundaries are enforced in code, not in prompts. "
                "No escalation of its own authority is possible from within "
                "the system. Only the owner can elevate the tier.",
        "enforcement": "code_enforced",
    },
    {
        "num": 6,
        "title": "Maintain an unbreakable audit trail.",
        "text": "Every autonomous action is logged in an append-only record "
                "that InterGen cannot modify or delete. The audit trail is "
                "proof that InterGen did what it said it did. Tampering with "
                "the audit trail is a governance violation equivalent to the "
                "underlying action being unauthorized.",
        "enforcement": "code_enforced",
    },
    {
        "num": 7,
        "title": "Roll back before rolling forward.",
        "text": "Before any self-directed change, InterGen must ensure it can "
                "undo it. A change without a rollback path is a Tier 4 action "
                "regardless of its scope.",
        "enforcement": "code_enforced",
    },
    {
        "num": 8,
        "title": "Never exceed the pace of trust.",
        "text": "InterGen earns expanded autonomy through demonstrated "
                "reliability, not self-assessment. The owner decides when "
                "to expand authority based on observed behavior over time.",
        "enforcement": "code_enforced",
    },
    {
        "num": 9,
        "title": "Cooldown protects against hasty decisions.",
        "text": "Repeated actions within the same category in a short window "
                "trigger mandatory escalation. The system cannot be rushed "
                "into approving a series of changes that should be reviewed "
                "as a group.",
        "enforcement": "code_enforced",
    },
    {
        "num": 10,
        "title": "The owner's voice is final.",
        "text": "In any conflict between InterGen's autonomous judgment and "
                "the owner's explicit instruction, the owner wins. Always. "
                "Without exception. InterGen may surface information that "
                "suggests reconsideration, but once the owner decides, "
                "InterGen executes. The system exists to serve, not to govern.",
        "enforcement": "prompt_anchored",
    },
]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class GovernanceCheck:
    """Result of a single governance gate check."""
    gate_name: str
    passed: bool
    reason: str = ""
    detail: dict | None = None


@dataclass
class GovernanceDecision:
    """Complete governance decision for a tool call."""
    tool_call_id: str
    allowed: bool
    blocked_by: str = ""          # which gate blocked, if any
    checks: list[GovernanceCheck] = field(default_factory=list)
    tier_required: int = 0        # what tier this action requires
    tier_current: int = 0         # what tier the agent is currently at
    cooldown_notice: str = ""     # if cooldown triggered
    escalation_recommended: bool = False


@dataclass
class CooldownEntry:
    """Tracks per-category action timing for cooldown enforcement."""
    tool_category: str
    last_approved_at: float       # monotonic timestamp
    approval_count_window: int    # approvals in current window
    window_start: float           # when the current window began


# ---------------------------------------------------------------------------
# Governance Engine
# ---------------------------------------------------------------------------

class GovernanceEngine:
    """Ring-0 governance enforcement engine.

    Hash-verified at startup. Validates every tool call against the tier
    system, cooldown rules, and owner-only action list BEFORE the
    provenance gate is reached.
    """

    def __init__(
        self,
        autonomy_tier: AutonomyTier = AutonomyTier.OBSERVE,
        tier_config_path: Path | None = None,
    ) -> None:
        self._autonomy_tier = autonomy_tier
        # None (the default) resolves per-process: /etc for root, XDG state for
        # a --user daemon (G3-15). An explicit path (tests) is honored as-is.
        self._tier_config_path = (
            tier_config_path if tier_config_path is not None
            else _resolve_tier_config_path())
        self._hash_verified = False
        self._last_hash_check = 0.0
        self._cooldowns: dict[str, CooldownEntry] = {}
        self._healthy = True

        # Cooldown windows per category (seconds)
        self._cooldown_window = 1800       # 30 minutes
        self._cooldown_threshold = 3       # 3+ approvals in window = escalate

    # -- Hash Verification ---------------------------------------------------

    def verify_hash(self) -> bool:
        """Verify that governance.py has not been tampered with.

        Hashes this module's installed source file and compares it to the
        build-established baseline at GOVERNANCE_HASH_PATH — shipped read-only
        by the intergen package into dm-verity-protected /usr/share. The
        baseline is set ONCE at build time by a trusted authority, NOT
        trust-on-first-use from the running daemon: TOFU would (a) fail on the
        read-only system path and (b) let a tampered module bless its own hash,
        defeating the check. A missing OR mismatched baseline fails CLOSED.
        """
        source_path = Path(__file__)
        if not source_path.exists():
            logger.error("Governance source file not found at %s", source_path)
            self._healthy = False
            self._hash_verified = False
            return False

        current_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()

        if not GOVERNANCE_HASH_PATH.exists():
            # No build-established baseline → integrity cannot be established.
            # Fail closed; never write a TOFU baseline from the daemon (the
            # intergen package ships this file, so its absence is itself an
            # integrity failure, not a first-run condition).
            logger.critical(
                "Governance baseline missing at %s — integrity cannot be "
                "established; failing closed.", GOVERNANCE_HASH_PATH,
            )
            self._healthy = False
            self._hash_verified = False
            return False

        stored_hash = GOVERNANCE_HASH_PATH.read_text().strip().split()[0]
        if stored_hash != current_hash:
            logger.critical(
                "GOVERNANCE HASH MISMATCH — possible tampering. "
                "Expected: %s, Actual: %s",
                stored_hash[:16], current_hash[:16],
            )
            self._healthy = False
            self._hash_verified = False
            self._write_audit("hash_verify_fail", {
                "stored_hash_prefix": stored_hash[:16],
                "current_hash_prefix": current_hash[:16],
            }, "GOVERNANCE HASH MISMATCH — possible tampering")
            return False

        self._hash_verified = True
        self._last_hash_check = time.monotonic()
        return True

    def periodic_hash_check(self) -> bool:
        """Re-verify hash periodically at runtime."""
        if time.monotonic() - self._last_hash_check > HASH_RECHECK_INTERVAL:
            return self.verify_hash()
        return self._hash_verified

    @property
    def healthy(self) -> bool:
        return self._healthy and self._hash_verified

    # -- Autonomy Tier -------------------------------------------------------

    @property
    def autonomy_tier(self) -> AutonomyTier:
        return self._autonomy_tier

    def set_tier(self, tier: AutonomyTier, owner_confirmed: bool = False) -> bool:
        """Change the autonomy tier. MUST be owner-confirmed."""
        old_tier = self._autonomy_tier
        if not owner_confirmed:
            logger.warning(
                "Tier change attempted without owner confirmation: %s -> %s",
                self._autonomy_tier.name, tier.name,
            )
            self._write_audit("tier_change", {
                "from_tier": int(old_tier),
                "from_tier_name": old_tier.name,
                "to_tier": int(tier),
                "to_tier_name": tier.name,
                "owner_confirmed": False,
                "succeeded": False,
            }, f"tier_change DENIED: {old_tier.name}->{tier.name} (no owner confirmation)")
            return False
        if tier > self._autonomy_tier:
            logger.info(
                "Autonomy tier elevated: %s -> %s (owner confirmed)",
                self._autonomy_tier.name, tier.name,
            )
        self._autonomy_tier = tier
        self._persist_tier()
        self._write_audit("tier_change", {
            "from_tier": int(old_tier),
            "from_tier_name": old_tier.name,
            "to_tier": int(tier),
            "to_tier_name": tier.name,
            "owner_confirmed": True,
            "succeeded": True,
        }, f"tier_change: {old_tier.name}->{tier.name} (owner confirmed)")
        return True

    def _persist_tier(self) -> None:
        # Atomic write: render to a temp file in the same dir, then os.replace
        # (atomic rename). A crash/power-loss mid-write can then never leave a
        # truncated tier file that would brick the next load_tier(). (Was a
        # plain write_text → truncation possible.)
        #
        # Fail SAFE, never crash: a write failure (e.g. a read-only target dir)
        # must not take down the owner-confirmed set_tier() call. The in-memory
        # tier is already updated; if persistence fails the worst case is the
        # tier reverts to the safe baseline (OBSERVE) on the next restart — the
        # conservative direction. We log + audit rather than propagate (G3-15).
        try:
            private_dir(self._tier_config_path.parent)
            payload = json.dumps({
                "autonomy_tier": int(self._autonomy_tier),
                "tier_name": self._autonomy_tier.name,
            }, indent=2)
            tmp = self._tier_config_path.with_suffix(
                self._tier_config_path.suffix + ".tmp")
            private_write_text(tmp, payload)
            os.replace(tmp, self._tier_config_path)
        except OSError as e:
            logger.warning("tier persist failed (%s): %s — tier %s is in effect "
                           "for this session but will not survive a restart",
                           type(e).__name__, e, self._autonomy_tier.name)
            try:
                self._write_audit("tier_persist", {
                    "succeeded": False,
                    "error": type(e).__name__,
                    "path": str(self._tier_config_path),
                    "tier": int(self._autonomy_tier),
                }, f"tier_persist FAILED ({type(e).__name__}) — tier "
                   f"{self._autonomy_tier.name} not persisted")
            except Exception:
                pass

    def load_tier(self) -> None:
        """Load the persisted tier from config.

        SECURITY (security-first posture): never crash on a corrupt/truncated file, and
        never silently TRUST an out-of-range value. set_tier() is the only
        owner-confirmed elevation path; a tampered or damaged tier file must
        not be able to either crash the daemon at startup or smuggle an
        elevated tier across a restart. On any anomaly we fall back to the
        safe baseline (OBSERVE) and record an audit event. (A stricter policy
        — always boot at OBSERVE and require live re-confirmation to elevate —
        is available if tier-persistence is ever deemed too much trust.)
        """
        if not self._tier_config_path.exists():
            return
        try:
            data = json.loads(self._tier_config_path.read_text())
            loaded = AutonomyTier(int(data["autonomy_tier"]))
        except (OSError, ValueError, KeyError, TypeError) as e:
            self._autonomy_tier = AutonomyTier.OBSERVE
            try:
                self._write_audit("tier_load", {
                    "succeeded": False,
                    "error": type(e).__name__,
                    "fallback_tier": int(AutonomyTier.OBSERVE),
                }, f"tier_load FAILED ({type(e).__name__}) — fell back to OBSERVE")
            except Exception:
                logger.warning("tier_load failed and audit write also failed: %s", e)
            return
        self._autonomy_tier = loaded

    def tier_required_for(self, risk_tier: str, tool_name: str) -> AutonomyTier:
        """Determine the minimum autonomy tier required for an action."""
        if tool_name in OWNER_ONLY_ACTIONS:
            return AutonomyTier.OWNER
        return TIER_REQUIRED_FOR_RISK.get(risk_tier, AutonomyTier.OWNER)

    # -- Cooldown Enforcement (from anti-manipulation research) --------------

    def check_cooldown(self, tool_category: str) -> tuple[bool, str]:
        """Check if this action triggers a cooldown escalation.

        Returns (should_escalate, notice).

        From preceding project approval flow security research: 'Repeated actions
        within the same category in a short window trigger mandatory
        escalation.' This prevents the 'salami attack' pattern where
        an adversary convinces the system to approve many small changes
        that collectively constitute a large unauthorized change.
        """
        now = time.monotonic()
        entry = self._cooldowns.get(tool_category)

        if entry is None or (now - entry.window_start) > self._cooldown_window:
            # New window
            self._cooldowns[tool_category] = CooldownEntry(
                tool_category=tool_category,
                last_approved_at=now,
                approval_count_window=1,
                window_start=now,
            )
            return False, ""

        # Within window — increment
        entry.last_approved_at = now
        entry.approval_count_window += 1

        if entry.approval_count_window >= self._cooldown_threshold:
            notice_text = (
                f"{entry.approval_count_window} {tool_category} actions "
                f"approved in the last "
                f"{int((now - entry.window_start) / 60)} minutes. "
                f"Escalating to owner review per Commandment 9 (Cooldown)."
            )
            self._write_audit("cooldown_escalation", {
                "tool_category": tool_category,
                "approval_count": entry.approval_count_window,
                "window_minutes": int((now - entry.window_start) / 60),
                "threshold": self._cooldown_threshold,
            }, notice_text)
            return True, notice_text

        return False, ""

    def record_approval(self, tool_category: str) -> None:
        """Record that an action was approved (for cooldown tracking)."""
        # check_cooldown already increments — this is for paths where
        # check_cooldown wasn't called (e.g., auto-approved actions)
        now = time.monotonic()
        entry = self._cooldowns.get(tool_category)
        if entry is None or (now - entry.window_start) > self._cooldown_window:
            self._cooldowns[tool_category] = CooldownEntry(
                tool_category=tool_category,
                last_approved_at=now,
                approval_count_window=1,
                window_start=now,
            )

    # -- The Core Enforcement Method -----------------------------------------

    def evaluate(
        self,
        tool_call_id: str,
        tool_name: str,
        risk_tier: str,
        tool_category: str = "",
    ) -> GovernanceDecision:
        """Evaluate a tool call against all governance rules.

        Called by tool_registry.execute() BEFORE the provenance gate.
        Returns a GovernanceDecision with:
        - allowed: bool — can this action proceed to the gate?
        - blocked_by: str — which rule blocked it (empty if allowed)
        - checks: list[GovernanceCheck] — all gate results for transparency

        Enforcement order (per research: deterministic first, LLM last):
        1. Hash integrity (is governance itself intact?)
        2. Tier gate (is the agent authorized for this risk level?)
        3. Owner-only gate (is this an owner-physical-action?)
        4. Cooldown gate (has this been approved too many times too fast?)
        """
        checks: list[GovernanceCheck] = []
        blocked: list[str] = []

        # Gate 1: Hash integrity
        if not self.healthy:
            checks.append(GovernanceCheck(
                gate_name="hash_integrity",
                passed=False,
                reason="Governance module hash verification failed — "
                       "possible tampering. All autonomous actions suspended.",
            ))
            return GovernanceDecision(
                tool_call_id=tool_call_id,
                allowed=False,
                blocked_by="hash_integrity",
                checks=checks,
            )

        checks.append(GovernanceCheck(
            gate_name="hash_integrity", passed=True,
        ))

        # Gate 2: Tier check
        required = self.tier_required_for(risk_tier, tool_name)

        checks.append(GovernanceCheck(
            gate_name="tier_check",
            passed=self._autonomy_tier >= required,
            reason=f"Requires {required.name}, current {self._autonomy_tier.name}" if self._autonomy_tier < required else "OK",
            detail={
                "required_tier": int(required),
                "required_tier_name": required.name,
                "current_tier": int(self._autonomy_tier),
                "current_tier_name": self._autonomy_tier.name,
            },
        ))

        if self._autonomy_tier < required:
            blocked.append("tier_check")

        # Gate 3: Owner-only actions — must be Tier 4 regardless of risk
        if tool_name in OWNER_ONLY_ACTIONS:
            checks.append(GovernanceCheck(
                gate_name="owner_only",
                passed=self._autonomy_tier == AutonomyTier.OWNER,
                reason=f"'{tool_name}' requires physical owner access (Tier 4)",
                detail={"action_category": "owner_only"},
            ))
            if self._autonomy_tier != AutonomyTier.OWNER:
                blocked.append("owner_only")
        else:
            checks.append(GovernanceCheck(
                gate_name="owner_only", passed=True, reason="Not an owner-only action",
            ))

        # Gate 4: Cooldown check
        if tool_category:
            should_escalate, notice = self.check_cooldown(tool_category)
            checks.append(GovernanceCheck(
                gate_name="cooldown",
                passed=not should_escalate,
                reason=notice if should_escalate else "OK",
            ))
            if should_escalate:
                blocked.append("cooldown")
        else:
            checks.append(GovernanceCheck(
                gate_name="cooldown", passed=True, reason="No category specified",
            ))

        # Assemble decision
        blocked_by = blocked[0] if blocked else ""

        if blocked_by:
            self._write_audit("evaluation_blocked", {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "risk_tier": risk_tier,
                "tier_required": int(required),
                "tier_required_name": required.name,
                "tier_current": int(self._autonomy_tier),
                "tier_current_name": self._autonomy_tier.name,
                "blocked_by": blocked_by,
                "blocked_by_full": blocked,
            }, f"evaluation BLOCKED by {blocked_by}: {tool_name} (tier {self._autonomy_tier.name} < {required.name})")

        return GovernanceDecision(
            tool_call_id=tool_call_id,
            allowed=len(blocked) == 0,
            blocked_by=blocked_by,
            checks=checks,
            tier_required=int(required),
            tier_current=int(self._autonomy_tier),
            cooldown_notice=checks[-1].reason if blocked_by == "cooldown" else "",
            escalation_recommended=blocked_by == "cooldown",
        )

    def _write_audit(self, event_type: str, details: dict[str, Any],
                     summary: str) -> None:
        from intergen.audit_log import write_record, default_log_path
        from intergen.interfaces.provenance import AuditRecord

        record = AuditRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            tool_name=event_type,
            arguments={},
            declared_provenance="",
            effective_provenance="",
            ingress_tools_this_turn=[],
            user_decision="",
            result_summary=summary[:256],
            source_attribution="intergen/governance.py",
            kind="governance_decision",
            event_type=event_type,
            details=details,
        )
        write_record(record, default_log_path())

    # -- Health --------------------------------------------------------------

    def health_snapshot(self) -> dict:
        """Return current governance health for the web UI."""
        return {
            "hash_verified": self._hash_verified,
            "healthy": self.healthy,
            "autonomy_tier": int(self._autonomy_tier),
            "autonomy_tier_name": self._autonomy_tier.name,
            "active_cooldowns": len(self._cooldowns),
            "hash_path": str(GOVERNANCE_HASH_PATH),
            "last_hash_check": self._last_hash_check,
        }

    def get_commandments(self) -> list[dict]:
        """Return the commandments for the governance dashboard."""
        return COMMANDMENTS
