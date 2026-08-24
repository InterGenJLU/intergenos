# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""InterGen semantic matcher — 4-layer intent resolution.

Ported from a prior internal AI assistant project. Enhanced with:
- Layer 1: regex/keyword matching (new in this implementation)
- Higher default thresholds (0.90 vs 0.85 — system commands are dangerous)
- Thread-safe registration via lock
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from intergen.interfaces.semantic import MatchResult, SemanticMatcherInterface

if TYPE_CHECKING:  # numpy is a Layer-2-only dep — keep it out of the runtime import
    import numpy as np


def _np():
    """Import numpy lazily.

    numpy is needed only by the Layer-2 embedding paths (_embed,
    _match_embeddings, _cosine_similarity); Layer 0/1 keyword + regex matching
    needs none of it. Importing it at module level made
    ``from intergen.semantic import SemanticMatcher`` HARD-CRASH the daemon on a
    first-run system where numpy is not yet installed (it ships in the AI
    runtime deps installed by ``intergen setup``, not at image build). Loading
    it on demand keeps this module importable without numpy so the embedding
    layer degrades off instead of taking the whole daemon down.
    """
    import numpy as np
    return np

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


# Bounded leading/trailing FILLER stripped in _normalize_input so a polite/soft
# imperative reaches the same keyword as the bare form. LONGEST-FIRST so a
# multi-word phrase is tried before a prefix of it. Conservative on purpose — only
# unambiguous request-framing filler, never content words.
_LEADING_FILLER = (
    "go ahead and", "why don't you", "why dont you", "i need you to",
    "i want you to", "i would like you to", "can you please", "could you please",
    "can you", "could you", "would you", "will you", "please", "just", "kindly",
)
_TRAILING_FILLER = (
    "for me please", "for me", "please", "already", "right now", "now", "thanks",
    "thank you",
)


class SemanticMatcher(SemanticMatcherInterface):
    """4-layer semantic matching for intent resolution.

    Layer 1: Regex/keyword (<1ms)
    Layer 2: Embedding similarity (10-50ms)
    Layer 3: LLM tool calling (handled by router, not here)
    Layer 4: LLM free response (handled by router, not here)
    """

    def __init__(self,
                 embedder: Callable[[list[str]], list[list[float]] | None] | None = None,
                 *, model_name: str = "nomic-ai/nomic-embed-text-v1.5"):
        """AI-12: embeddings come from the local llama.cpp /embedding server,
        NOT sentence-transformers/torch/huggingface.

        `embedder` is a callable list[str] -> list[list[float]] | None — e.g.
        a started embedding LlamaManager's .embed method. If it is None or the
        embedding server is unavailable, Layer-2 embedding matching is skipped
        gracefully (Layer 0/1 keyword matching + the router's LLM layers still
        work). `model_name` is retained for provenance/logging only — the model
        is served as GGUF by the llama-server, not loaded in-process.
        """
        self._keyword_intents: list[_KeywordIntent] = []
        self._embedding_intents: dict[str, _EmbeddingIntent] = {}
        # Embedding intents whose examples could NOT be embedded at registration
        # time because the embed backend was down (the GDM-greeter cold-boot port
        # collision, or the first-boot provisioning window). Retained here as
        # (examples, threshold, tool_name) rather than dropped, so that when the
        # embedder recovers, refresh_pending_intents() can build their vectors and
        # promote them into _embedding_intents — making the embed server's
        # self-heal recover the FULL intent corpus, not just the live server.
        self._pending_embedding_intents: dict[
            str, tuple[list[str], float, str | None]] = {}
        self._lock = threading.Lock()
        self._embedder = embedder
        self._model_name = model_name

    def _embed(self, texts: list[str]) -> np.ndarray | None:
        """Embed texts via the injected llama.cpp embedder.

        Returns an (n, dim) float32 array, or None when no embedder is
        configured, the embedding server is unavailable, OR the returned vectors
        are not a well-formed numeric matrix — callers degrade gracefully rather
        than crash.
        """
        if self._embedder is None:
            return None
        # Guard the embedder CALL itself, not just the shape conversion below: a
        # live embedder can RAISE (a transient connection error, or a malformed
        # non-shape response from a server that is up), and an unguarded raise
        # would propagate out of a per-query match() and, at startup, make intent
        # registration raise into the keyword-only fallback with Layer-2 dead for
        # the session. Degrade any embedder exception to None — identical to a
        # cleanly-down embedder — so registration, match, and refresh are all
        # uniformly safe (WC recovery-path red-team).
        try:
            vectors = self._embedder(list(texts))
        except Exception as e:
            logger.warning("semantic._embed: embedder raised (%s); degrading to "
                           "None", type(e).__name__)
            return None
        if not vectors:
            return None
        # Do not trust the embedder's SHAPE: a ragged (uneven-length) or
        # non-numeric response (version mismatch / error envelope / proxy — the
        # shapes the embed path explicitly distrusts) makes np.asarray(float32)
        # raise. Degrade to None like every other malformed case rather than
        # letting it propagate to the caller (startup intent registration / a
        # per-query match()). Robust regardless of the embedder's own guarantees.
        try:
            np = _np()
            return np.asarray(vectors, dtype=np.float32)
        except (ValueError, TypeError) as e:
            logger.warning("semantic._embed: malformed embedding shape (%s); "
                           "degrading to None", type(e).__name__)
            return None

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

        embeddings = self._embed(examples)
        if embeddings is None:
            # No embedding backend (server down / not configured) at registration.
            # Do NOT drop the intent: retain it as PENDING so the embed watchdog's
            # recovery (refresh_pending_intents) can register it once the embedder
            # comes back, instead of leaving Layer-2 permanently short for a
            # session that started with the embedder down. The router still
            # resolves the query via its LLM layers until then.
            with self._lock:
                self._pending_embedding_intents[intent_id] = (
                    list(examples), threshold, tool_name)
            logger.warning(
                "Embedding intent %s PENDING — no embedding backend at "
                "registration (llama.cpp /embedding server not up); will register "
                "on embedder recovery", intent_id,
            )
            return

        with self._lock:
            self._embedding_intents[intent_id] = _EmbeddingIntent(
                intent_id=intent_id,
                embeddings=embeddings,
                examples=examples,
                threshold=threshold,
                tool_name=tool_name,
            )
            # Registered live — clear any stale pending entry for this id.
            self._pending_embedding_intents.pop(intent_id, None)

        logger.debug("Registered embedding intent: %s (%d examples, threshold=%.2f)",
                      intent_id, len(examples), threshold)

    def refresh_pending_intents(self) -> int:
        """Re-register embedding intents skipped when the embed backend was down
        at registration. Called by the embedding watchdog on a restart-success,
        so the embed server's self-heal recovers the FULL intent corpus (not just
        a live embed callable with no vectors behind it).

        Returns the number of intents promoted from pending to active this call.

        Robust by design (red-teamed):
        - Retry-safe: an intent leaves pending ONLY once its embedding actually
          succeeds; a still-down or transient-empty embed keeps it pending so a
          subsequent recovery retries it, so the skip-on-failure bug cannot just
          move down a level.
        - Thread-safe: the (slow) embed calls run OUTSIDE the lock; the lock is
          held only for the snapshot of pending and for the atomic merge into
          _embedding_intents — so a concurrent match() never sees a half-built
          set. match() snapshots _embedding_intents under the same lock.
        - Best-effort: never raises into the watchdog thread; a per-intent embed
          failure is contained and the rest still proceed.
        """
        with self._lock:
            pending = dict(self._pending_embedding_intents)
        if not pending:
            return 0

        recovered: dict[str, _EmbeddingIntent] = {}
        for intent_id, (examples, threshold, tool_name) in pending.items():
            try:
                embeddings = self._embed(examples)
            except Exception as e:  # never let one bad intent abort recovery
                logger.warning("refresh_pending_intents: embed failed for %s "
                               "(%s); kept pending", intent_id,
                               type(e).__name__)
                continue
            if embeddings is None:
                # Embedder still not returning vectors — kept pending, retried
                # on a subsequent recovery.
                continue
            recovered[intent_id] = _EmbeddingIntent(
                intent_id=intent_id,
                embeddings=embeddings,
                examples=examples,
                threshold=threshold,
                tool_name=tool_name,
            )

        if not recovered:
            return 0

        with self._lock:
            for intent_id, intent in recovered.items():
                self._embedding_intents[intent_id] = intent
                self._pending_embedding_intents.pop(intent_id, None)

        logger.info("refresh_pending_intents: re-registered %d embedding intent(s) "
                    "after embedder recovery (%d still pending)",
                    len(recovered), len(self._pending_embedding_intents))
        return len(recovered)

    def match(self, query: str) -> MatchResult:
        """Match query through Layer 0 (normalize) → Layer 1 (keyword) → Layer 2 (embedding).

        Returns the best match. intent_id is None if below threshold.
        """
        normalized = self._normalize_input(query)

        result = self._match_keywords(normalized)
        if result.intent_id is not None:
            return result

        # Try fragments — expand bare words into implicit queries
        expanded = self._expand_fragment(normalized)
        if expanded != normalized:
            result = self._match_keywords(expanded)
            if result.intent_id is not None:
                return result

        return self._match_embeddings(normalized)

    @staticmethod
    def _normalize_input(query: str) -> str:
        """Layer 0: clean up messy user input before matching.

        Handles: contractions, common misspellings, stray punctuation,
        case normalization. Real users type 'whats my hostnam?' not
        'What is my hostname?'
        """
        q = query.strip()
        if not q:
            return q

        # Common contractions
        _CONTRACTIONS = {
            "whats": "what's", "hows": "how's", "whos": "who's",
            "wheres": "where's", "thats": "that's", "dont": "don't",
            "doesnt": "doesn't", "isnt": "isn't", "cant": "can't",
            "wont": "won't", "im": "i'm", "ive": "i've",
            "youre": "you're", "theyre": "they're",
        }

        # Common system term misspellings
        _TYPO_FIXES = {
            "hostnam": "hostname", "hostnme": "hostname",
            "servce": "service", "serivce": "service", "sevice": "service",
            "pacakge": "package", "packge": "package", "pakage": "package",
            "memroy": "memory", "memeory": "memory", "memmory": "memory",
            "stoarge": "storage", "storge": "storage",
            "netowrk": "network", "netwrok": "network",
            "kernal": "kernel", "kernl": "kernel",
            "direcotry": "directory", "directroy": "directory",
            "conifg": "config", "confg": "config",
            "systme": "system", "sytem": "system",
            "restrat": "restart", "restatr": "restart",
            "insatll": "install", "isntall": "install",
            "unisntall": "uninstall",
            "firwall": "firewall", "firewal": "firewall",
            # "is ssh runnign?" mis-routed to freeform and the 2B fabricated a
            # fake systemctl dump; the service-status pattern needs "running".
            "runnign": "running", "runing": "running", "runing": "running",
            "runnning": "running", "runnig": "running",
            "actvie": "active", "actie": "active", "enabld": "enabled",
        }

        words = q.split()
        fixed = []
        for word in words:
            lower = word.lower().rstrip("?!.,;:")
            if lower in _CONTRACTIONS:
                fixed.append(_CONTRACTIONS[lower])
            elif lower in _TYPO_FIXES:
                fixed.append(_TYPO_FIXES[lower])
            else:
                fixed.append(word)

        result = " ".join(fixed)
        # Strip bounded leading/trailing FILLER so a polite/soft imperative reaches
        # the same ^verb keyword as the bare form: "just remove docker already" ->
        # "remove docker", "could you pull up the processes" -> "pull up the
        # processes". Bounded phrase sets only — never content words — and stripping
        # trailing filler keeps arg extraction clean (package='docker', not
        # 'already'). Teaching carries the prior INSIDE ("could you tell me how to
        # X" -> "tell me how to X" still hits the explain prior), so it is unaffected.
        low = result.lower()
        for lead in _LEADING_FILLER:  # longest-first
            if low.startswith(lead + " ") and len(result) > len(lead) + 1:
                result = result[len(lead) + 1:]
                low = result.lower()
                break
        for trail in _TRAILING_FILLER:
            if low.endswith(" " + trail) and len(result) > len(trail) + 1:
                result = result[:-(len(trail) + 1)]
                break
        return result.strip() or q

    @staticmethod
    def _expand_fragment(query: str) -> str:
        """Expand bare fragments into implicit system queries.

        'hostname?' → 'what is my hostname'
        'disk' → 'show disk usage'
        'services' → 'list services'
        """
        q = query.lower().strip().rstrip("?!.")

        _FRAGMENT_MAP = {
            "hostname": "what is my hostname",
            "kernel": "what kernel am I running",
            "ip": "what is my ip address",
            "disk": "show disk usage",
            "memory": "show memory usage",
            "ram": "show memory usage",
            "cpu": "show cpu info",
            "uptime": "show uptime",
            "services": "list running services",
            "packages": "list installed packages",
            "network": "show network interfaces",
            "gpu": "what is my gpu",
            "os": "os version",
            "storage": "show disk usage",
            "firewall": "show firewall status",
        }

        if q in _FRAGMENT_MAP:
            return _FRAGMENT_MAP[q]

        # Single word + question mark pattern: "disk?" → expand
        if len(q.split()) == 1 and q in _FRAGMENT_MAP:
            return _FRAGMENT_MAP[q]

        return query

    def _match_keywords(self, query: str) -> MatchResult:
        """Layer 1: regex/keyword matching.

        Tries the raw query AND its fragment expansion ("storage?" -> "show disk
        usage"). The router's P1 calls this directly (not match()), so without
        the expansion here a bare-noun fragment misses every keyword pattern and
        falls to the LLM tool path — where the 2B may decline to call the tool
        and fabricate incapability (the lex_disk_terse "storage?" defect). The
        matched INTENT is returned; tool execution still runs on the raw query,
        whose fragment word (disk/storage/ram/...) is itself a command selector.
        """
        for q in (query, self._expand_fragment(query)):
            for intent in self._keyword_intents:
                for pattern in intent.patterns:
                    if pattern.search(q):
                        return MatchResult(
                            intent_id=intent.intent_id,
                            score=1.0,
                            layer="keyword",
                            tool_name=intent.tool_name,
                        )
        return MatchResult(intent_id=None, score=0.0, layer="keyword")

    def _match_embeddings(self, query: str) -> MatchResult:
        """Layer 2: embedding similarity matching."""
        # Snapshot the intent set under the lock so a concurrent
        # refresh_pending_intents() on the watchdog thread (promoting pending
        # intents mid-query) can never mutate the dict we iterate — the dict
        # changing size mid-iteration would otherwise raise. The slow query embed
        # and the similarity loop then run on this immutable snapshot, off-lock.
        with self._lock:
            intents = list(self._embedding_intents.values())
        if not intents:
            return MatchResult(intent_id=None, score=0.0, layer="embedding")

        query_arr = self._embed([query])
        if query_arr is None:
            # Embedding backend unavailable — degrade to no Layer-2 match.
            return MatchResult(intent_id=None, score=0.0, layer="embedding")
        query_emb = query_arr[0]

        np = _np()
        # SELECT THE BEST ELIGIBLE CANDIDATE, AND REPORT ITS OWN SCORE.
        #
        # These were once one running pair: `best_score` advanced for any candidate
        # that beat it, while the intent name and tool advanced only for a candidate
        # that ALSO cleared its own threshold. Per-intent thresholds differ across
        # the shipped corpus, so a higher-scoring INELIGIBLE candidate could raise
        # the reported score while the reported name and tool still belonged to a
        # different, lower-scoring one. The caller's admission gate (score >= 0.85)
        # was then satisfied by a number that did not belong to the intent being
        # admitted, and — with the ineligible candidate registered first — an
        # eligible candidate could be dropped entirely, which made this layer
        # depend on registration order.
        #
        # Selection is now an argmax over the candidates that clear their OWN
        # threshold, which is order-free, and the returned name, tool and score all
        # describe that one candidate.
        best_intent = None
        best_tool = None
        best_eligible_score = 0.0
        # Observability, not selection. `top_score` is the highest similarity any
        # candidate reached regardless of eligibility, so a near-miss under an
        # intent's own bar stays visible; `runner_up_score` is the best score among
        # the candidates that were NOT selected, which is the top1-top2 ambiguity
        # gap the decision trace records at the P2 seam.
        top_score = 0.0
        scores: list[float] = []

        for intent in intents:
            sims = self._cosine_similarity(query_emb, intent.embeddings)
            max_sim = float(np.max(sims))
            scores.append(max_sim)
            if max_sim > top_score:
                top_score = max_sim
            if max_sim >= intent.threshold and max_sim > best_eligible_score:
                best_eligible_score = max_sim
                best_intent = intent.intent_id
                best_tool = intent.tool_name

        if best_intent is None:
            # Nothing cleared its own threshold. Report no intent and no borrowed
            # score; the near-miss remains visible in top_score.
            return MatchResult(
                intent_id=None,
                score=0.0,
                layer="embedding",
                tool_name=None,
                runner_up_score=0.0,
                top_score=top_score,
            )

        others = sorted((s for s in scores if s != best_eligible_score),
                        reverse=True)
        # A tie on the winning score still leaves a genuine runner-up.
        if scores.count(best_eligible_score) > 1:
            others.insert(0, best_eligible_score)
        runner_up = others[0] if others else 0.0

        return MatchResult(
            intent_id=best_intent,
            score=best_eligible_score,
            layer="embedding",
            tool_name=best_tool,
            runner_up_score=runner_up,
            top_score=top_score,
        )

    @staticmethod
    def _cosine_similarity(vec1: np.ndarray, vec2: np.ndarray) -> np.ndarray:
        """Cosine similarity between a query vector and a batch of vectors."""
        np = _np()
        if vec2.ndim == 1:
            vec2 = vec2.reshape(1, -1)
        dot = np.dot(vec2, vec1)
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2, axis=1)
        return dot / (norm1 * norm2 + 1e-8)

    def get_intent_count(self) -> int:
        """Return total number of registered intents."""
        return len(self._keyword_intents) + len(self._embedding_intents)
