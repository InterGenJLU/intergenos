"""InterGen semantic matcher — 4-layer intent resolution.

Ported from JARVIS core/semantic_matcher.py. Enhanced with:
- Layer 1: regex/keyword matching (new, not in JARVIS)
- Higher default thresholds (0.90 vs 0.85 — system commands are dangerous)
- Thread-safe registration via lock
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from intergen.interfaces.semantic import MatchResult, SemanticMatcherInterface

logger = logging.getLogger(__name__)


@dataclass
class _KeywordIntent:
    intent_id: str
    patterns: list[re.Pattern]
    tool_name: str | None


@dataclass
class _EmbeddingIntent:
    intent_id: str
    embeddings: np.ndarray
    examples: list[str]
    threshold: float
    tool_name: str | None


class SemanticMatcher(SemanticMatcherInterface):
    """4-layer semantic matching for intent resolution.

    Layer 1: Regex/keyword (<1ms)
    Layer 2: Embedding similarity (10-50ms)
    Layer 3: LLM tool calling (handled by router, not here)
    Layer 4: LLM free response (handled by router, not here)
    """

    def __init__(self, model_name: str = "nomic-ai/nomic-embed-text-v1.5",
                 cache_dir: str | None = None,
                 device: str = "cpu"):
        self._keyword_intents: list[_KeywordIntent] = []
        self._embedding_intents: dict[str, _EmbeddingIntent] = {}
        self._lock = threading.Lock()
        self._model = None
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._device = device

    def _ensure_model(self) -> None:
        """Lazy-load the embedding model on first use."""
        if self._model is not None:
            return
        import warnings
        warnings.filterwarnings("ignore", message=".*resume_download.*")
        from sentence_transformers import SentenceTransformer
        logger.info("Loading embedding model: %s (device=%s)",
                     self._model_name, self._device)
        self._model = SentenceTransformer(
            self._model_name,
            cache_folder=self._cache_dir,
            device=self._device,
            trust_remote_code=True,
        )
        logger.info("Embedding model loaded")

    def register_keyword_pattern(self, intent_id: str, patterns: list[str], *,
                                 tool_name: str | None = None) -> None:
        """Register regex patterns for Layer 1 matching."""
        compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
        with self._lock:
            self._keyword_intents.append(
                _KeywordIntent(intent_id=intent_id, patterns=compiled,
                               tool_name=tool_name)
            )
        logger.debug("Registered keyword intent: %s (%d patterns)",
                      intent_id, len(patterns))

    def register_intent(self, intent_id: str, examples: list[str], *,
                        threshold: float = 0.90,
                        tool_name: str | None = None) -> None:
        """Register embedding-based intent for Layer 2 matching."""
        if not examples:
            raise ValueError(f"Intent {intent_id} has no examples")

        self._ensure_model()
        embeddings = self._model.encode(
            examples, convert_to_numpy=True, show_progress_bar=False
        )

        with self._lock:
            self._embedding_intents[intent_id] = _EmbeddingIntent(
                intent_id=intent_id,
                embeddings=embeddings,
                examples=examples,
                threshold=threshold,
                tool_name=tool_name,
            )

        logger.debug("Registered embedding intent: %s (%d examples, threshold=%.2f)",
                      intent_id, len(examples), threshold)

    def match(self, query: str) -> MatchResult:
        """Match query through Layer 1 (keyword) then Layer 2 (embedding).

        Returns the best match. intent_id is None if below threshold.
        """
        result = self._match_keywords(query)
        if result.intent_id is not None:
            return result

        return self._match_embeddings(query)

    def _match_keywords(self, query: str) -> MatchResult:
        """Layer 1: regex/keyword matching."""
        for intent in self._keyword_intents:
            for pattern in intent.patterns:
                if pattern.search(query):
                    return MatchResult(
                        intent_id=intent.intent_id,
                        score=1.0,
                        layer="keyword",
                        tool_name=intent.tool_name,
                    )
        return MatchResult(intent_id=None, score=0.0, layer="keyword")

    def _match_embeddings(self, query: str) -> MatchResult:
        """Layer 2: embedding similarity matching."""
        if not self._embedding_intents:
            return MatchResult(intent_id=None, score=0.0, layer="embedding")

        self._ensure_model()
        query_emb = self._model.encode(
            [query], convert_to_numpy=True, show_progress_bar=False
        )[0]

        best_intent = None
        best_score = 0.0
        best_tool = None

        for intent in self._embedding_intents.values():
            sims = self._cosine_similarity(query_emb, intent.embeddings)
            max_sim = float(np.max(sims))

            if max_sim > best_score:
                best_score = max_sim
                if max_sim >= intent.threshold:
                    best_intent = intent.intent_id
                    best_tool = intent.tool_name

        return MatchResult(
            intent_id=best_intent,
            score=best_score,
            layer="embedding",
            tool_name=best_tool,
        )

    @staticmethod
    def _cosine_similarity(vec1: np.ndarray, vec2: np.ndarray) -> np.ndarray:
        """Cosine similarity between a query vector and a batch of vectors."""
        if vec2.ndim == 1:
            vec2 = vec2.reshape(1, -1)
        dot = np.dot(vec2, vec1)
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2, axis=1)
        return dot / (norm1 * norm2 + 1e-8)

    def get_intent_count(self) -> int:
        """Return total number of registered intents."""
        return len(self._keyword_intents) + len(self._embedding_intents)
