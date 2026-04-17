"""InterGen compound query decomposer — tier-aware task splitting.

Detects multi-action queries and decomposes them based on hardware tier.
Ported from JARVIS core/task_planner.py compound detection, enhanced
with tier-aware thresholds from ADaPT and DAAO research.

Design:
  - Fast compound detection (regex, no LLM, microseconds)
  - Tier-aware thresholds determine when to decompose
  - Tone: competent, not apologetic
  - Above threshold: "Let me take those one at a time"
  - Below threshold: attempt monolithic (let the LLM handle it)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from intergen.interfaces.types import HardwareTierLevel

logger = logging.getLogger(__name__)

# Tier → max compound actions before decomposition
_TIER_THRESHOLDS = {
    HardwareTierLevel.TIER_1: 1,   # 2B model: one action at a time
    HardwareTierLevel.TIER_2: 3,   # 9B model: up to 3 compound actions
    HardwareTierLevel.TIER_3: 5,   # 35B model: up to 5 compound actions
}

# Conjunctive phrases that signal compound requests (from JARVIS TaskPlanner)
_COMPOUND_SIGNALS = [
    r"\band\s+then\b",
    r"\band\s+also\b",
    r"\bthen\s+(?:also\s+)?(?:check|show|start|stop|restart|install|remove|run|open|list|display)",
    r"\bafter\s+that\b",
    r"\balso\s+(?:check|show|start|stop|restart|install|remove|run|open|list|display)",
    r"\bfirst\b.*\bthen\b",
    r"\b(?:plus|additionally)\b",
    r"\band\s+(?:what|how|show|check|display|list|tell|is|are)\b",
]

_COMPOUND_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _COMPOUND_SIGNALS]

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
    action_count = count_actions(query) if is_compound else 1
    # If compound detected but verb counting underestimates (e.g. "what X and what Y"
    # deduplicates "what" to 1), try split_compound to get the real count.
    if is_compound and action_count <= 1:
        tentative_split = split_compound(query)
        if len(tentative_split) > 1:
            action_count = len(tentative_split)
    threshold = _TIER_THRESHOLDS.get(tier, 3)
    needs_decomposition = is_compound and action_count > threshold

    result = DecomposedQuery(
        is_compound=is_compound,
        action_count=action_count,
        needs_decomposition=needs_decomposition,
    )

    if needs_decomposition:
        result.sub_queries = split_compound(query)
        result.response_prefix = _build_decomposition_message(
            action_count, result.sub_queries
        )
        logger.info("Compound query detected: %d actions, tier %s threshold %d — decomposing",
                     action_count, tier.value, threshold)

    return result


def split_compound(query: str) -> list[str]:
    """Split a compound query into individual sub-queries.

    Uses conjunctive phrases as split points. Falls back to
    sentence splitting if no conjunctions found.
    """
    # Split on conjunctive signals
    split_pattern = re.compile(
        r"\s*(?:and\s+then|and\s+also|after\s+that|then\s+also|"
        r"additionally|plus|"
        r"and\s+(?=what\b|how\b|show\b|check\b|display\b|list\b|tell\b|is\b|are\b))\s*",
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
