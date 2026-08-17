# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""InterGen Sentinel — pluggable content-scanner interface.

Part 1 of the Sentinel + phone-a-friend mandate (consolidated design plan
2026-05-30, decided). This is the vendor-neutral interface the
scanner engine implements; concrete scanners live in `intergen/scanner/`.

A Scanner inspects a single piece of content travelling across a trust
boundary in one direction:

    EGRESS  — arguments going OUT to an external/MCP surface (exfil risk).
    INGRESS — content coming IN before it re-enters the LLM context
              (injection risk).

and returns a `ScanVerdict` whose `disposition` is one of:

    ALLOW — nothing of concern; let it through.
    FLAG  — suspicious; hold for a human modal (review_modal).
    BLOCK — high-confidence malicious; hard refuse / withhold.

The engine composes scanners through `ScannerPolicy` (see
`intergen/scanner/policy.py`): the always-on `LocalRulesScanner` runs first
as a deterministic floor, and most-severe disposition wins so ambiguity
defaults toward deny (security-only-alignment rule #10). The scanner slots into the
existing `ToolRegistry.execute()` dispatch chokepoint alongside the AI-6
gate and the AI-2 spotlight — it composes, it is not a bolt-on.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class ScanDirection(Enum):
    """Which way the content is crossing the trust boundary."""

    EGRESS = "egress"    # args leaving the machine toward an external surface
    INGRESS = "ingress"  # content arriving before it re-enters LLM context


class ScanDisposition(Enum):
    """A scanner's verdict on one piece of content.

    Ordered by severity (ALLOW < FLAG < BLOCK) so the policy can merge
    multiple verdicts with "most-severe wins" — the default-deny-on-ambiguity
    posture per security-only-alignment rule #10.
    """

    ALLOW = "allow"  # nothing of concern
    FLAG = "flag"    # suspicious -> human modal
    BLOCK = "block"  # high-confidence malicious -> hard refuse / withhold

    @property
    def severity(self) -> int:
        """Integer rank for most-severe-wins merging (higher = more severe)."""
        return _SEVERITY[self]


_SEVERITY = {
    ScanDisposition.ALLOW: 0,
    ScanDisposition.FLAG: 1,
    ScanDisposition.BLOCK: 2,
}


def most_severe(a: ScanDisposition, b: ScanDisposition) -> ScanDisposition:
    """Return the more severe of two dispositions (default-deny on ambiguity)."""
    return a if a.severity >= b.severity else b


@dataclass
class ScanContext:
    """Metadata about the content being scanned.

    Carried alongside the content so a scanner can reason about WHERE the
    content is and WHICH way it flows. `trust_tier` carries the caller's
    trust/provenance label (e.g. an `MCPTrustTier` value or a `Provenance`
    label such as "user_direct"/"ingress_derived") when the caller has one;
    the scanner engine itself does not require it, and the chokepoint wiring
    (Part 2) supplies whatever label it holds.
    """

    surface: str                 # e.g. "mcp:<server>/<tool>" | "web_search" | "file:<path>"
    direction: ScanDirection
    tool_name: str = ""
    trust_tier: str | None = None


@dataclass
class ScanVerdict:
    """The result of scanning one piece of content."""

    disposition: ScanDisposition
    reason: str = ""
    score: float = 0.0          # 0..1 confidence in the disposition
    scanner: str = ""           # name of the scanner that produced this
    categories: list[str] = field(default_factory=list)

    @classmethod
    def allow(cls, scanner: str = "") -> "ScanVerdict":
        """Convenience constructor for a clean ALLOW verdict."""
        return cls(disposition=ScanDisposition.ALLOW, scanner=scanner)


class Scanner(ABC):
    """Abstract content scanner — one direction, one verdict.

    Implementations: `LocalRulesScanner` (always-on deterministic floor),
    `LocalQwenScanner` (local llama.cpp classifier, on-demand), and
    `CloudScanner` (opt-in, wraps a vendor-neutral cloud adapter).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable scanner identifier (e.g. 'local-rules')."""

    @property
    @abstractmethod
    def is_local(self) -> bool:
        """True if the scan runs fully on-device (no network)."""

    @abstractmethod
    def scan(self, content: str, ctx: ScanContext) -> ScanVerdict:
        """Inspect `content` for `ctx.direction` and return a verdict."""
