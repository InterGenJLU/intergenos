# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Discovery-run policy review responder (M8-6 leg B, component 1).

The r30 `INTERGEN_TEST_REVIEW_AUTOPILOT` is binary allow|deny. The mass
discovery run needs a POLICY instead: read-only tool classes ALLOW (their real
results enrich the trace), mutating classes DENY-AND-RECORD (the STAGED
tool+args IS the routing observation — zero side effects, zero prompts), and
privileged stays fail-closed-DENY (the r30 hard constraint, untouched).

This is implemented at the harness-responder layer as a `review_callback`
(the single hook at `tool_registry.py:553 review_callback(call, review_decision)`).
It is injected onto the in-process daemon (`daemon._review_callback_override`)
by the discovery runner — NO product-code edit to dbus_daemon.py / client.py.

Why this is the whole policy, exactly:
  - READ_ONLY tools EXECUTE for all provenance and NEVER reach a review_callback
    (`provenance.py _BEHAVIOR_MATRIX` READ_ONLY -> "execute"; `tool_registry`
    read-only tools skip `must_review`). So "read-only ALLOW" needs no code — it
    is the upstream default. This callback only ever sees state-changing or
    egress-flagged calls.
  - Everything reaching the callback is therefore mutating (user-scope /
    hold_for_review) OR privileged (needs_pkexec). Both get DENIED; the staged
    (tool, args, tier) is RECORDED to the run's dispatch ledger + glass. The deny
    guarantees ZERO side effects and ZERO modal prompts (fully headless).
  - `needs_pkexec is True` <=> PRIVILEGED_STATE_CHANGING (provenance.py) — the
    fail-closed default (True on an unknown decision shape) preserves the r30
    privileged-always-deny constraint even if the decision object is malformed.

RED/GREEN (see test_discovery_policy.py): a mutating dispatch under this policy
NEVER executes AND its staged intent lands in the ledger; a read-only dispatch
executes normally and never calls back.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable

try:  # glass is always-on in the daemon; tolerate its absence in unit context
    from intergen import glass as _glass
except Exception:  # pragma: no cover - defensive
    _glass = None


@dataclass
class StagedDispatch:
    """One DENY-AND-RECORD observation: a staged (tool, args) that the policy
    refused so it never ran. This IS the routing observation for the run."""
    ts: float
    tool: str
    arguments: Any
    tier: str            # "mutating" | "privileged"
    verdict: str         # always "deny" under this policy
    reason: str = ""
    turn_hint: str = ""  # optional: the current user turn text, set by the runner


class DispatchLedger:
    """Thread-safe append-only sink for staged-and-denied dispatches, banked to
    <run_dir>/dispatch-ledger.jsonl alongside glass/decisions for the run-id."""

    def __init__(self, path: Path | None = None):
        self._lock = threading.Lock()
        self._records: list[StagedDispatch] = []
        self._path = path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, staged: StagedDispatch) -> None:
        with self._lock:
            self._records.append(staged)
            if self._path is not None:
                with self._path.open("a") as fh:
                    fh.write(json.dumps(asdict(staged), ensure_ascii=False, default=str) + "\n")

    def all(self) -> list[StagedDispatch]:
        with self._lock:
            return list(self._records)

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)


# a per-run hint the runner updates so a staged record can name the turn it came
# from without threading turn context through the daemon's dispatch chokepoint.
class _TurnHint:
    def __init__(self) -> None:
        self._v = ""
        self._lock = threading.Lock()

    def set(self, v: str) -> None:
        with self._lock:
            self._v = v

    def get(self) -> str:
        with self._lock:
            return self._v


def make_policy_review_callback(
    ledger: DispatchLedger,
    turn_hint: _TurnHint | None = None,
) -> Callable[[Any, Any], str]:
    """Build the discovery-run policy review_callback.

    Returns a callback with the `tool_registry.execute(review_callback=...)`
    contract: `(call, decision) -> "allow_once"|"allow_conversation"|"deny"|
    "deny_conversation"`. This policy ALWAYS returns "deny" (record-and-refuse)
    because — by construction — only mutating/privileged/egress-flagged calls
    ever reach it; read-only executes upstream and never calls back.
    """
    def _callback(call: Any, decision: Any) -> str:
        tool = getattr(call, "name", "?")
        args = getattr(call, "arguments", None)
        # fail-closed: unknown decision shape -> treat as privileged
        needs_pkexec = bool(getattr(decision, "needs_pkexec", True))
        tier = "privileged" if needs_pkexec else "mutating"
        reason = str(getattr(decision, "reason", "") or "")
        staged = StagedDispatch(
            ts=time.time(), tool=tool, arguments=args, tier=tier,
            verdict="deny", reason=reason,
            turn_hint=(turn_hint.get() if turn_hint else ""),
        )
        ledger.record(staged)
        if _glass is not None:
            try:
                _glass.emit("decision", "discovery_policy_deny", detail={
                    "tool": tool, "args": args, "tier": tier,
                    "verdict": "deny", "reason": reason,
                })
            except Exception:  # pragma: no cover - glass best-effort
                pass
        return "deny"

    return _callback
