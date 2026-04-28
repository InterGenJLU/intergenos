"""Semantic matching interface — 4-layer intent resolution."""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class MatchResult:
    """Result of semantic intent matching."""
    intent_id: str | None
    score: float
    layer: str
    tool_name: str | None = None


class SemanticMatcherInterface(ABC):
    """4-layer semantic matching (ported from a prior internal AI assistant project).

    Layer 1: Regex/keyword (<1ms) — catches 70-80% deterministically
    Layer 2: Embedding similarity (10-50ms) — nomic-embed-text-v1.5
    Layer 3: LLM tool calling (1-5s) — semantically pruned tool set
    Layer 4: LLM free response (1-3s) — quality gates + fallback

    Thresholds are higher than the prior implementation (0.85-0.95 vs 0.55-0.85)
    because system commands are dangerous.
    """

    @abstractmethod
    def register_intent(self, intent_id: str, examples: list[str], *,
                        threshold: float = 0.90,
                        tool_name: str | None = None) -> None:
        """Register an intent with example phrases.

        Args:
            intent_id: Unique identifier (e.g., 'system_disk_usage').
            examples: Example user phrases that match this intent.
            threshold: Minimum similarity score (higher = more conservative).
            tool_name: Associated tool name for routing.
        """

    @abstractmethod
    def match(self, query: str) -> MatchResult:
        """Match a query against all registered intents.

        Returns the best match. intent_id is None if below threshold.
        score is always populated (even if no match).
        """

    @abstractmethod
    def register_keyword_pattern(self, intent_id: str, patterns: list[str], *,
                                 tool_name: str | None = None) -> None:
        """Register regex/keyword patterns for Layer 1 matching.

        Args:
            intent_id: Unique identifier.
            patterns: List of regex patterns.
            tool_name: Associated tool name.
        """

    @abstractmethod
    def get_intent_count(self) -> int:
        """Return number of registered intents."""
