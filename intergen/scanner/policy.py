# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""ScannerPolicy — the scan chain (Sentinel design plan §2).

The composition rule:

    1. `LocalRulesScanner` ALWAYS runs first (deterministic floor).
    2. BLOCK from the floor short-circuits — stop, refuse.
    3. ALLOW at depth=baseline passes through.
    4. FLAG, or any scan at depth=deep, escalates to the configured deeper
       scanner (Local-Qwen by default / Cloud if opted-in).
    5. Most-severe disposition wins — ambiguity defaults toward deny
       (security-only-alignment rule #10). A scanner that errors fails CLOSED to FLAG
       (we could not confirm the content safe -> hold for a human), never to
       a silent ALLOW.

The deeper scanner is optional and is None until the Local-Qwen / Cloud
scanners land (build sequence step 4). Until then a FLAG from the floor
stands as FLAG and is handed to the human modal at the chokepoint — which is
the correct, safe behaviour.
"""

from __future__ import annotations

import logging
from enum import Enum

from intergen.interfaces.scanner import (
    Scanner,
    ScanContext,
    ScanDisposition,
    ScanVerdict,
    most_severe,
)
from intergen.scanner.local_rules import LocalRulesScanner

logger = logging.getLogger(__name__)


class ScanDepth(Enum):
    """How deep to scan. baseline = rules floor only; deep = always escalate."""

    BASELINE = "baseline"
    DEEP = "deep"


class ScannerPolicy:
    """Composes the always-on rules floor with an optional deeper scanner."""

    def __init__(
        self,
        local_rules: Scanner | None = None,
        deep_scanner: Scanner | None = None,
        default_depth: "ScanDepth | None" = None,
    ) -> None:
        self._local: Scanner = local_rules or LocalRulesScanner()
        self._deep: Scanner | None = deep_scanner
        # The depth used when scan() is called without an explicit one — lets the
        # chokepoint's 2-arg scan(content, ctx) honor a config-set posture
        # (baseline = floor, escalate only on FLAG; deep = always escalate).
        self._default_depth: ScanDepth = default_depth or ScanDepth.BASELINE

    def set_deep_scanner(self, scanner: Scanner | None) -> None:
        """Attach (or detach) the deeper scanner used on FLAG / depth=deep."""
        self._deep = scanner

    def scan(
        self,
        content: str,
        ctx: ScanContext,
        depth: "ScanDepth | None" = None,
    ) -> ScanVerdict:
        if depth is None:
            depth = self._default_depth
        local_verdict = self._run(self._local, content, ctx)

        # The floor's BLOCK is final — refuse without spending the deeper scan.
        if local_verdict.disposition is ScanDisposition.BLOCK:
            return local_verdict

        need_deep = (
            local_verdict.disposition is ScanDisposition.FLAG
            or depth is ScanDepth.DEEP
        )
        if not need_deep or self._deep is None:
            return local_verdict

        deep_verdict = self._run(self._deep, content, ctx)
        return _merge(local_verdict, deep_verdict)

    @staticmethod
    def _run(scanner: Scanner, content: str, ctx: ScanContext) -> ScanVerdict:
        """Run one scanner, failing CLOSED to FLAG on any error (HG #10)."""
        try:
            return scanner.scan(content, ctx)
        except Exception as exc:  # noqa: BLE001 — fail closed, never silently allow
            logger.warning(
                "Scanner %s raised %s during scan; failing closed to FLAG",
                getattr(scanner, "name", scanner.__class__.__name__),
                type(exc).__name__,
            )
            return ScanVerdict(
                disposition=ScanDisposition.FLAG,
                reason=f"scanner-error-fail-closed: {type(exc).__name__}",
                score=0.6,
                scanner=getattr(scanner, "name", scanner.__class__.__name__),
                categories=["scanner.error"],
            )


def _merge(a: ScanVerdict, b: ScanVerdict) -> ScanVerdict:
    """Merge two verdicts: most-severe disposition wins; categories unioned.

    The verdict that determined the winning disposition (higher score breaks a
    severity tie; the local floor `a` breaks a full tie) supplies the reason
    and score so the human-facing modal shows the deciding rationale.
    """
    winner_disposition = most_severe(a.disposition, b.disposition)

    candidates = [v for v in (a, b) if v.disposition is winner_disposition]
    primary = max(candidates, key=lambda v: v.score)  # a precedes b -> ties keep local

    categories = list(a.categories)
    for cat in b.categories:
        if cat not in categories:
            categories.append(cat)

    return ScanVerdict(
        disposition=winner_disposition,
        reason=primary.reason,
        score=primary.score,
        scanner=primary.scanner,
        categories=categories,
    )
