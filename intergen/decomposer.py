# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""InterGen compound query decomposer — splits multi-action queries.

Detects multi-action queries and splits any genuine multi-part request so every
clause is answered. Ported from a prior internal AI assistant project's compound
detection, enhanced with research from ADaPT and DAAO.

Design:
  - Fast compound detection (regex, no LLM, microseconds)
  - M5 RESTRAINT (blueprint move, 2B-compensation removal): decomposition no
    longer fires on ARITHMETIC — "plus" is an operator, not a conjunction, so
    "2 plus 2" is one un-decomposed turn (the anti-lobotomy win: the 9B answers
    it natively). It DOES still detect explicit sequencing (and then / after
    that / first...then / then <verb>), imperative-joining (and/also
    <action-verb>), and interrogative-joining (and <what/how/...>) as compounds.
    Whether a detected compound is actually SPLIT is the router's call (M5
    completion): the router decomposes only a MIXED compound — one with a
    fast-path clause the locked model cannot fetch (system state / action) — and
    hands a PURE-KNOWLEDGE compound to the model WHOLE (the 9B holds a two-part
    question natively), with the single-value fast-paths stepping aside so no
    clause is answered in isolation. Interrogative-joining detection is kept
    precisely so the router can see the mixed case ("hostname and what year was
    Linux created") and split it — its first clause a single-value fast-path
    would otherwise intercept while SILENTLY DROPPING the second. Silent loss is
    forbidden absolutely (the security-only mandate is supreme); the router
    handoff plus decomposition together guarantee every clause is answered.
  - Substance guard: every sub-query must carry a real content word — a
    noun/object, NOT a pure function/bare-verb token and NOT a lone number (an
    arithmetic operand like "2" is not a substantive sub-query).
  - When it DOES split, each part routes independently so none is silently
    dropped, and each mutating action is reviewed on its own (defense in depth).
  - Tone: competent, not apologetic — shaped by the action count.
  - The hardware-tier threshold (_TIER_THRESHOLDS) is DIAGNOSTIC ONLY: it is
    recorded in the log/trace and gates NOTHING. The real gate is strong signals
    + substance (see analyze_query); the log names that gate, not the threshold.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from intergen import glass
from intergen.interfaces.types import HardwareTierLevel

logger = logging.getLogger(__name__)

# Tier → historical per-model action sizing. DIAGNOSTIC ONLY: recorded in the
# decomposition log line; does NOT gate the split — analyze_query decomposes any
# >=2 substantive sub-queries regardless of tier (see the module docstring).
_TIER_THRESHOLDS = {
    HardwareTierLevel.TIER_1: 1,   # 2B model sizing (historical)
    HardwareTierLevel.TIER_2: 3,   # 9B model sizing (historical)
    HardwareTierLevel.TIER_3: 5,   # 35B model sizing (historical)
}


@dataclass(frozen=True)
class DecompositionCapCheck:
    """Result of the per-tier decomposition-cap validation hook.

    ``tier_cap`` is the tier's historical action-sizing threshold; ``split_count``
    is how many substantive sub-queries the split produced; ``over_cap`` is True
    when the split exceeds the tier cap. ``enforcing`` records whether the caller
    treated the cap as a hard limit — it is False everywhere today (the cap stays
    diagnostic, per the module docstring), so the hook only OBSERVES. A separate
    reviewed step can switch a caller to enforcing once the trace/harness data
    shows a per-tier cap improves answer quality.
    """
    tier: HardwareTierLevel
    tier_cap: int
    split_count: int
    over_cap: bool
    enforcing: bool = False


def validate_decomposition_cap(
    sub_queries: list[str],
    tier: HardwareTierLevel,
    enforcing: bool = False,
) -> DecompositionCapCheck:
    """Check a decomposition split against the tier's action-sizing cap.

    This is a HOOK, not a gate: with ``enforcing=False`` (the only mode wired
    today) it reports whether ``len(sub_queries)`` exceeds ``_TIER_THRESHOLDS``
    for the tier and changes nothing. The seam exists so the per-tier caps —
    long carried as diagnostic-only — can be validated against real traces and,
    once the data justifies it, switched to clamping/enforcing in one reviewed
    step without re-plumbing analyze_query.
    """
    cap = _TIER_THRESHOLDS.get(tier, 3)
    count = len(sub_queries)
    return DecompositionCapCheck(
        tier=tier,
        tier_cap=cap,
        split_count=count,
        over_cap=count > cap,
        enforcing=enforcing,
    )

# Conjunctive phrases that signal multi-part requests (M5 restraint).
#
# ARITHMETIC is gone: "plus" is an operator, not a conjunction — "2 plus 2" is
# one query (reinforced by the lone-number substance guard below). That is the
# clear anti-lobotomy win: the 9B answers arithmetic in one un-decomposed turn.
#
# INTERROGATIVE-JOINING is still DETECTED, deliberately. The 9B holds a
# pure-KNOWLEDGE two-parter natively — and the router now hands those to it whole
# (M5 completion). But a MIXED tool+knowledge one ("what's my hostname AND what
# year was Linux created") has a clause a single-value fast-path would intercept
# (the cache answered the hostname and SILENTLY DROPPED the Linux-year clause in
# the battery). Silent loss is the one thing the security-only mandate forbids
# absolutely. So detection stays on "and <interrogative>" and the router splits
# the compound whenever a clause is fast-path-carriable
# (_compound_has_fastpath_clause), routing whole only when every clause is pure
# knowledge the model can answer without a fast-path.
_IMPERATIVE_VERBS = (
    r"check|show|display|list|start|stop|restart|enable|disable|install|"
    r"remove|uninstall|run|execute|open|launch|search|tell"
)
_INTERROGATIVES = r"what|how|is|are|which|when|where|who|why|does|do|can"
_COMPOUND_SIGNALS = [
    r"\band\s+then\b",
    r"\bafter\s+that\b",
    r"\bfirst\b.*\bthen\b",
    rf"\bthen\s+(?:also\s+)?(?:{_IMPERATIVE_VERBS})\b",
    rf"\b(?:and\s+)?also\s+(?:{_IMPERATIVE_VERBS})\b",
    rf"\band\s+(?:{_IMPERATIVE_VERBS})\b",
    rf"\band\s+(?:{_INTERROGATIVES})\b",
    r"\badditionally\b",
]

_COMPOUND_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _COMPOUND_SIGNALS]

# Function words + bare action verbs that, ALONE, do not constitute a request.
# Used to reject a split where one side is a contentless fragment — e.g.
# "Could you please look up and tell me what the hostname is" splits on "and
# tell" into ["...look up", "tell me what the hostname is"], but "look up" has no
# object (the object lives in the other clause), so it is ONE request, not a
# compound. A real sub-query carries a content word (a noun/object) beyond these.
_FRAGMENT_TOKENS = frozenset(
    "could would can will please you your i me my we our the a an of to up on in at "
    "for with and or but is are was were be been being do does did this that these "
    "those it its as so now currently set kind sort just really very also then look "
    "tell give get show find see know want need let go come make take put run use "
    "look-up lookup".split()
)
_WORD_RE = re.compile(r"[a-zA-Z0-9_./-]+")
# A purely-numeric token — an arithmetic operand ("2", "2.5", "3/4"), NOT a
# substantive noun/object. Excluded from the content-word test (M5): "2 plus 2"
# split into "...2" / "2" must NOT read as two substantive sub-queries.
_NUMERIC_TOKEN = re.compile(r"^[0-9]+(?:[.,/_-][0-9]+)*$")


def _clause_has_content(clause: str) -> bool:
    """True if the clause carries a content word — a real noun/object, not a
    pure function/bare-verb token and not a lone number. A lone arithmetic
    operand ("2") is not substance, so "2 plus 2" is one query, not a compound."""
    for t in _WORD_RE.findall(clause.lower()):
        if t in _FRAGMENT_TOKENS or _NUMERIC_TOKEN.match(t):
            continue
        return True
    return False

# Action verbs that indicate distinct operations
_ACTION_VERBS = re.compile(
    r"\b(?:check|show|display|list|start|stop|restart|enable|disable|"
    r"install|remove|uninstall|run|execute|open|launch|search|find|"
    r"read|write|create|delete|update|tell|what|how)\b",
    re.IGNORECASE,
)


@dataclass
class DecomposedQuery:
    """Result of compound query analysis."""
    is_compound: bool
    action_count: int
    needs_decomposition: bool
    sub_queries: list[str] = field(default_factory=list)
    response_prefix: str = ""


def detect_compound(query: str) -> bool:
    """Fast compound detection — regex only, no LLM. Microseconds."""
    for pattern in _COMPOUND_PATTERNS:
        if pattern.search(query):
            return True
    return False


def count_actions(query: str) -> int:
    """Estimate the number of distinct actions in a query."""
    matches = _ACTION_VERBS.findall(query)
    return max(1, len(set(m.lower() for m in matches)))


def analyze_query(query: str, tier: HardwareTierLevel) -> DecomposedQuery:
    """Analyze a query for compound actions and determine if decomposition is needed.

    Args:
        query: User's input text.
        tier: Current hardware tier level.

    Returns:
        DecomposedQuery with analysis results.
    """
    is_compound = detect_compound(query)
    if not is_compound:
        glass.emit("decision", "decompose", detail={
            "is_compound": False, "needs_decomposition": False})
        return DecomposedQuery(
            is_compound=False, action_count=1, needs_decomposition=False,
        )

    sub_queries = split_compound(query)
    # Real distinct-action count: verb counting can underestimate
    # ("what X and what Y" dedups "what" to 1), so floor it at the split size.
    action_count = max(count_actions(query), len(sub_queries))
    threshold = _TIER_THRESHOLDS.get(tier, 3)

    # Decompose any GENUINE multi-part request (>=2 distinct sub-queries),
    # regardless of tier. The tier threshold used to gate whether to split, on
    # the premise that a larger model could answer N parts in one monolithic
    # pass — but a single-value fast-path (cache/identity/keyword) or a single
    # LLM tool-call structurally cannot answer multiple parts, so a below-
    # threshold compound had a clause SILENTLY DROPPED: "What's my hostname and
    # what year was Linux created?" hit the single-value cache, which answered
    # the hostname and never saw the second clause. Splitting routes each part
    # independently so every part is answered (and each mutating action is
    # reviewed on its own — defense in depth). The hardware-tier threshold no
    # longer gates the split at all: it is computed only for the diagnostic log
    # line below. (This is why the CPU-only box that detects as TIER_2 but runs
    # the 2B override has no functional mismatch here — the TIER_2 threshold of 3,
    # sized for the 9B, never changes a decomposition decision.) The response tone
    # is shaped by action_count, which is independent of the threshold.
    #
    # Substance guard: every sub-query must carry a content word. This rejects a
    # split where one side is a contentless fragment ("...look up" in "look up and
    # tell me what the hostname is") — a single verbose request, not a compound.
    needs_decomposition = (
        len(sub_queries) >= 2
        and all(_clause_has_content(s) for s in sub_queries)
    )
    matched = [p.pattern for p in _COMPOUND_PATTERNS if p.search(query)]

    result = DecomposedQuery(
        is_compound=is_compound,
        action_count=action_count,
        needs_decomposition=needs_decomposition,
        sub_queries=sub_queries if needs_decomposition else [],
    )

    if needs_decomposition:
        result.response_prefix = _build_decomposition_message(
            action_count, result.sub_queries
        )
        # Log the REAL gate (M5): strong multi-action signals AND >=2 substantive
        # sub-queries. The hardware-tier threshold gates nothing — it is named as
        # diagnostic-only, never as the reason for the split.
        logger.info("Compound decomposed: %d substantive sub-queries on strong "
                     "signals %s (tier %s threshold %d = diagnostic-only, ungated)",
                     len(sub_queries), matched, tier.value, threshold)

    # M1 (bullet 3): the decomposer verdict WITH the matched signals. Post-M5 the
    # gate is strong_signals+substance; the threshold is carried but marked
    # diagnostic-only so the trace can never be misread as threshold-gated.
    # Per-tier decomposition-cap validation hook (diagnostic-only, enforcing=False):
    # observe whether the split exceeds the tier cap, surface it on the trace, and
    # change nothing about the split decision. The seam lets the caps be validated
    # against real traces before any decision to make them enforcing.
    cap_check = validate_decomposition_cap(sub_queries, tier)
    glass.emit("decision", "decompose", detail={
        "is_compound": True, "matched_signals": matched,
        "sub_queries": sub_queries, "action_count": action_count,
        "gate": "strong_signals+substance",
        "threshold": threshold, "threshold_is_diagnostic_only": True,
        "tier_cap": cap_check.tier_cap,
        "split_over_tier_cap": cap_check.over_cap,
        "cap_enforcing": cap_check.enforcing,
        "needs_decomposition": needs_decomposition})

    return result


def split_compound(query: str) -> list[str]:
    """Split a compound query into individual sub-queries.

    Uses conjunctive phrases as split points. Falls back to
    sentence splitting if no conjunctions found.
    """
    # Split on the M5 conjunctive signals: sequencing + imperative-joining +
    # interrogative-joining. No "plus" (arithmetic) — that stays one query.
    split_pattern = re.compile(
        rf"\s*(?:and\s+then|after\s+that|then\s+also|additionally|"
        rf"(?:and\s+)?also\s+(?={_IMPERATIVE_VERBS}\b)|"
        rf"and\s+(?={_IMPERATIVE_VERBS}\b)|"
        rf"and\s+(?={_INTERROGATIVES}\b))\s*",
        re.IGNORECASE,
    )

    parts = split_pattern.split(query)
    parts = [p.strip().rstrip(".,;") for p in parts if p.strip()]

    if len(parts) <= 1:
        # Try splitting on "then" alone
        parts = re.split(r"\s*\bthen\b\s*", query, flags=re.IGNORECASE)
        parts = [p.strip().rstrip(".,;") for p in parts if p.strip()]

    if len(parts) <= 1:
        # Try splitting on commas followed by action verbs
        parts = re.split(r",\s*(?=(?:check|show|start|stop|restart|install|"
                          r"remove|run|open|list|display|tell|what|how)\b)",
                          query, flags=re.IGNORECASE)
        parts = [p.strip().rstrip(".,;") for p in parts if p.strip()]

    # Clean up: remove leading "and", "also", etc.
    cleaned = []
    for part in parts:
        part = re.sub(r"^(?:and|also|then|first|finally)\s+", "", part,
                      flags=re.IGNORECASE).strip()
        if part:
            cleaned.append(part)

    return cleaned if len(cleaned) > 1 else [query]


def _build_decomposition_message(action_count: int,
                                  sub_queries: list[str]) -> str:
    """Build the user-facing decomposition message.

    Tone: competent, not apologetic. Not 'I can't handle this'
    but 'Let me take them one at a time so I get each one right.'
    """
    if action_count == 2:
        return ("I see two things you'd like done. Let me take them "
                "one at a time — starting with the first.")
    elif action_count <= 4:
        return (f"I see {action_count} things you'd like done. Let me take "
                "them one at a time so I get each one right.")
    else:
        return (f"That's a lot to tackle at once — {action_count} actions. "
                "Let me work through them one at a time.")
