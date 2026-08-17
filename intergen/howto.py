# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""InterGen teaching how-to corpus — curated, VERIFIED answers for the explain intent.

Sibling to ``reference.py``. Where :class:`ReferenceIndex` grounds the FREEFORM path
with facts the small model paraphrases, this corpus serves curated, verified answers
*directly* for the EXPLAIN intent (PI-218-2). A 2B model emitting system commands from
its own weights is a silent-failure risk — it will confidently hand a user ``apt
install`` or wrong ``pkm`` syntax. For the high-frequency / high-risk how-tos we hand
the user a CHECKED answer, not a generated guess: "turn unverified assumptions into
checked gates" applied to the assistant's own output (security-first).

Retrieval is RAG over the already-running ``nomic-embed`` model — the SAME embedder the
:class:`~intergen.semantic.SemanticMatcher` uses, injected in, no new model loaded. A
deterministic keyword-overlap fallback keeps the corpus answering when the embedding
server is down (graceful degradation, never go dark).

Design invariants (mirroring reference.py):
  - READ-ONLY, in-tree, query-scoped. No new writable state.
  - GROUND-TRUTH FILTERED. An entry whose ``requires`` binaries are not on PATH is
    never surfaced (so the corpus cannot teach a command for a tool that was removed).
  - SINGLE SOURCE OF TRUTH. Every entry carries a ``doc_source`` — the wiki/``docs``
    page it must agree with — so the teaching corpus and the documentation cannot drift.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:  # numpy is an embedding-path-only dep — keep it out of the import
    import numpy as np

    from intergen.reference import ReferenceIndex

logger = logging.getLogger(__name__)


def _np():
    """Import numpy lazily (embedding path only) — keeps this module importable on a
    first-run system before the AI runtime deps are installed, exactly like
    ``intergen.semantic``; the corpus degrades to the keyword fallback instead of
    taking the daemon down."""
    import numpy as np
    return np


# Resolution order (mirrors intergen.voice / model_manager): explicit env override
# -> shipped system dir -> in-repo dev copy. The build installs the curated corpus
# to the system dir (read-only, on dm-verity, under the AI-immutable /usr/share/
# prefix so the AI can never rewrite its own teaching content); the in-tree copy is
# the dev fallback when running from source.
_ENV_DIR = "INTERGEN_HOWTO_DIR"
_SYSTEM_DIR = Path("/usr/share/intergen/howto")
_REPO_DIR = Path(__file__).resolve().parent / "data" / "howto"


def _default_data_dir() -> Path:
    """The first existing corpus dir in resolution order; the system dir if none
    exists yet (so the 'absent' warning names the shipped path)."""
    env = os.environ.get(_ENV_DIR)
    if env and Path(env).is_dir():
        return Path(env)
    if _SYSTEM_DIR.is_dir():
        return _SYSTEM_DIR
    return _REPO_DIR if _REPO_DIR.is_dir() else _SYSTEM_DIR


# A retrieval below this cosine is treated as "no curated answer" — the caller then
# answers generatively (still suppressing the auto-execute) rather than serving a
# weakly-related how-to. nomic-embed paraphrase cosines run high; 0.72 keeps genuine
# paraphrases while rejecting unrelated queries. Callers may override per context.
DEFAULT_THRESHOLD = 0.72
# The floor for a query that arrives WITHOUT an instructional prior (a plain
# imperative, which is an action to dispatch) or that names no procedure at all
# (an orientation ask) — only a strong match may capture those as teaching. On
# the embedding scale; the caller asks for it with ``strong=True`` rather than
# passing a number, because a floor is only meaningful on the scale of the path
# that produced the score.
STRONG_THRESHOLD = 0.82

# ── the keyword fallback's floors — MEASURED, not chosen ───────────────────
#
# When the embedding server is unavailable the corpus scores by word overlap
# instead of cosine, and a machine has been observed serving on that path. Its
# floors were originally chosen; these are read off the corpus itself.
#
# CALIBRATION (measured 2026-08-06 against the shipped corpus — 160 entries,
# 414 triggers; harness: scripts/howto-keyword-calibration.py, which reproduces
# every figure here):
#   * POSITIVES — 306 held-out-trigger queries: each of the 306 triggers
#     belonging to an entry with two or more phrasings, removed from the index
#     and then asked. The corpus demonstrably covers each one, so each must be
#     served. Their query-normalized overlap runs 0.0-1.0, median 0.5.
#   * NEGATIVES — 20 off-corpus questions sharing ordinary words with real
#     triggers. The binding one is "how do I add a user to my gym membership":
#     it CONTAINS the real trigger "how do I add a user", so every word-overlap
#     score puts it at 0.5, inside the positive range. No single overlap floor
#     separates it without also refusing legitimate questions that add context
#     words — which is why there are two conditions, not one.
#   * What DOES separate it: its extra words (gym, membership) are unknown to
#     the corpus. Measured as the share of a query's content words present in
#     the corpus's trigger vocabulary, the off-corpus negatives reach at most
#     0.5 among those that clear the overlap floor, while the positives sit at
#     median 1.0 with a 5th percentile of 0.5. KEYWORD_MIN_KNOWN_SHARE is the
#     lowest value that clears every measured negative.
#   * At these two floors: 238/306 positives served (77.8%) and 0/20 negatives,
#     against 133/306 (43.5%) and 1/20 for the previous single chosen floor of
#     0.5 on a Jaccard score — the earlier floor both refused most questions
#     the corpus covers AND served the hardest negative, because 0.5 was
#     exactly that negative's score and the gate admits equality.
#   * Each floor is the LOWEST value that clears every measured negative: the
#     off-corpus negatives peak at 0.3333 and the next positive score above
#     that is 0.4, so any floor in (0.3333, 0.4] gives identical verdicts and
#     0.34 is the one written down. Same rule for the strong floor below.
#   * MEASURED AGAINST THE HEALTHY PATH, which is what the degraded path stands
#     in for: run through the live embedding server, the same 306 positives are
#     served 277 times (90.5%). The keyword floors above agree with the
#     embedding path's served/refused verdict on 249/306 (81.4%), and that is
#     the maximum available — agreement is flat at 249 for every floor up to
#     0.4 and falls away above it. So the floor that the off-corpus negatives
#     force from below is also the floor that agrees best with the healthy
#     path; the two criteria pick the same number rather than trading off.
#   * One measured disagreement runs the OTHER way and is a finding about the
#     embedding floor, not this one: the embedding path SERVES the gym-
#     membership negative (cosine 0.8480 against DEFAULT_THRESHOLD 0.72) while
#     the keyword path now refuses it. DEFAULT_THRESHOLD is outside this
#     calibration's scope and is left as it stands.
#
# WHAT THIS CANNOT DO, stated rather than implied: word overlap cannot tell a
# question the corpus answers from a question that merely reuses its words. It
# reliably rejects a query whose subject the corpus has never heard of, which is
# the off-corpus case; it does not certify that a served answer is the BEST one
# in the corpus. Entry selection on this path is measured at 50.8% exact-entry
# (against 71.1% for the embedding path on the same queries), and the gap is
# dominated by the corpus carrying several entries that teach the same task —
# "how do I take a screenshot" exists twice, under two ids, in two domain files.
# Serving either is a correct answer; the measurement counts only one of them as
# exact, so 50.8% is a floor on how often this path is right, not a ceiling.
KEYWORD_THRESHOLD = 0.34
KEYWORD_MIN_KNOWN_SHARE = 0.60
# The keyword-scale counterpart of STRONG_THRESHOLD, measured the same way
# against the queries the router actually sends with strong required — plain
# imperatives (an action to dispatch, not a lesson) and orientation asks that
# name no procedure. Of the 19 measured, the embedding path serves 5, all of
# them questions a trigger covers word for word (cosine 0.89-0.93); the 14 it
# refuses score at most 0.6667 on the keyword scale ("create a scripts folder",
# "remove the transmission package") while all five it serves score exactly
# 1.0. Any floor in (0.6667, 1.0] therefore reproduces the healthy path's
# verdict on 17 of the 19, and 0.68 is the lowest such value.
#
# THE TWO IT DOES NOT REPRODUCE are named below. An earlier draft of this floor
# sat at 0.51, measured against a strong-band population that did not yet
# include the action shapes; the full test suite caught it by way of a file-
# lifecycle offer that stopped firing, because the corpus began capturing
# "create a scripts folder" as teaching. The population here now carries those
# shapes so the same gap cannot reopen silently.
#
# TWO STRONG-BAND QUERIES CANNOT BE SEPARATED BY ANY FLOOR, and the harness
# names them rather than letting them pass quietly: "how do I use this" and
# "how does this work" reduce to one or two very common words that some trigger
# supplies in full, so they score 1.0 exactly as a real one-word question like
# "what is pkm" does. The embedding path refuses both; the keyword path serves
# them. That is the honest cost of word overlap standing in for meaning.
KEYWORD_STRONG_THRESHOLD = 0.68

_WORD_RE = re.compile(r"[a-z0-9]+")
# High-frequency words that carry no retrieval signal — excluded from the keyword
# fallback so "how do I ..." boilerplate doesn't inflate overlap with every entry.
_STOPWORDS = frozenset((
    "how", "do", "i", "to", "the", "a", "an", "my", "is", "what", "whats",
    "can", "you", "me", "of", "on", "in", "for", "and", "it", "this", "that",
    "with", "show", "tell", "explain", "command", "s",
))


@dataclass(frozen=True)
class HowtoAction:
    """The action a how-to answer can OFFER to perform (explain-first, then offer).

    Never auto-run: the router presents it behind a confirm and dispatches the
    command through the normal safety-gated tool path only on the user's yes."""

    command: str       # the exact command shape, e.g. "pkm upgrade"
    tool: str          # the InterGen tool that executes it, e.g. "manage_packages"


@dataclass(frozen=True)
class HowtoEntry:
    """One curated how-to: the verified answer + an optional offered action."""

    id: str
    domain: str
    triggers: tuple[str, ...]   # example phrasings — the retrieval/embedding index
    answer: str                 # the CURATED, verified answer (markdown)
    doc_source: str             # the wiki/docs page this must agree with (anti-drift)
    action: HowtoAction | None = None
    requires: tuple[str, ...] = ()   # binaries that must be on PATH to surface this


class HowtoCorpus:
    """Curated how-to corpus with RAG retrieval (+ keyword fallback).

    Construct with the SAME embedder callable the SemanticMatcher uses
    (``list[str] -> list[list[float]] | None``); pass the live :class:`ReferenceIndex`
    so entries are ground-truth-filtered by what is actually installed. Both are
    optional — with no embedder the corpus serves the keyword fallback; with no
    reference no ground-truth filter is applied (every entry is eligible)."""

    def __init__(
        self,
        embedder: Callable[[list[str]], "list[list[float]] | None"] | None = None,
        *,
        data_dir: "str | os.PathLike[str] | None" = None,
        reference: "ReferenceIndex | None" = None,
    ) -> None:
        self._embedder = embedder
        self._reference = reference
        self._entries: list[HowtoEntry] = []
        # Flattened trigger index: parallel lists of trigger text + owning entry idx.
        self._trigger_texts: list[str] = []
        self._trigger_owner: list[int] = []
        self._trigger_words: list[set[str]] = []
        # Every content word any trigger uses. The keyword fallback asks how
        # much of a query this vocabulary accounts for: a question whose subject
        # the corpus has never heard of is out of domain no matter how many
        # ordinary words it happens to share with a trigger.
        self._vocabulary: set[str] = set()
        self._embeddings: "np.ndarray | None" = None

        self._load(Path(data_dir) if data_dir is not None else _default_data_dir())
        self._build_index()

    # ── loading ──────────────────────────────────────────────────────────────

    def _load(self, data_dir: Path) -> None:
        """Read every ``*.json`` domain file (each a list of entry dicts). A
        malformed file is skipped with a warning, never fatal — a bad corpus file
        must degrade the teaching feature, not crash the daemon."""
        if not data_dir.is_dir():
            logger.warning("howto: corpus dir %s absent; teaching corpus empty", data_dir)
            return
        for path in sorted(data_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                logger.warning("howto: cannot read %s (%s); skipping", path.name,
                               type(exc).__name__)
                continue
            if not isinstance(raw, list):
                logger.warning("howto: %s is not a JSON list; skipping", path.name)
                continue
            for item in raw:
                entry = self._parse_entry(item, source_file=path.name)
                if entry is not None:
                    self._entries.append(entry)
        logger.info("howto: loaded %d curated how-to entries from %s",
                    len(self._entries), data_dir)

    @staticmethod
    def _parse_entry(item: object, *, source_file: str) -> "HowtoEntry | None":
        if not isinstance(item, dict):
            return None
        try:
            triggers = tuple(str(t) for t in item["triggers"] if str(t).strip())
            if not triggers:
                raise ValueError("no triggers")
            action_raw = item.get("action")
            action = None
            if isinstance(action_raw, dict) and action_raw.get("command"):
                action = HowtoAction(command=str(action_raw["command"]),
                                     tool=str(action_raw.get("tool", "")))
            return HowtoEntry(
                id=str(item["id"]),
                domain=str(item.get("domain", "")),
                triggers=triggers,
                answer=str(item["answer"]),
                doc_source=str(item.get("doc_source", "")),
                action=action,
                requires=tuple(str(r) for r in item.get("requires", ())),
            )
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("howto: malformed entry in %s (%s); skipping",
                           source_file, type(exc).__name__)
            return None

    def _build_index(self) -> None:
        """Flatten triggers and embed them once (if an embedder is available)."""
        for idx, entry in enumerate(self._entries):
            for trig in entry.triggers:
                self._trigger_texts.append(trig)
                self._trigger_owner.append(idx)
                trig_words = self._content_words(trig)
                self._trigger_words.append(trig_words)
                self._vocabulary |= trig_words
        if not self._trigger_texts or self._embedder is None:
            return
        vectors = self._embedder(list(self._trigger_texts))
        if not vectors:
            logger.warning("howto: embedder returned nothing; falling back to keyword "
                           "retrieval")
            return
        try:
            np = _np()
            arr = np.asarray(vectors, dtype=np.float32)
            if arr.ndim != 2 or arr.shape[0] != len(self._trigger_texts):
                raise ValueError("shape mismatch")
            self._embeddings = arr
        except (ValueError, TypeError) as exc:
            logger.warning("howto: malformed trigger embeddings (%s); keyword "
                           "fallback only", type(exc).__name__)
            self._embeddings = None

    # ── retrieval ────────────────────────────────────────────────────────────

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    def _eligible(self, entry: HowtoEntry) -> bool:
        """Ground-truth filter: every ``requires`` binary must be on PATH. Without a
        ReferenceIndex, no filtering (every entry eligible)."""
        if not entry.requires or self._reference is None:
            return True
        return all(self._reference.is_installed(t) for t in entry.requires)

    def retrieve(self, query: str, *, threshold: float | None = None,
                 strong: bool = False) -> "tuple[HowtoEntry | None, float]":
        """Return the best-matching curated entry + its score, or ``(None, 0.0)``.

        Uses embedding cosine when the index is built, else a deterministic
        keyword-overlap fallback. The returned entry is always ground-truth-eligible.

        ``strong=True`` asks for the higher floor the router needs when a query
        carries no instructional prior, or names no procedure. Ask for it with
        this flag rather than by passing a number: the two retrieval paths score
        on different scales, so a caller cannot know which number is meaningful
        until it knows which path ran. The floor is chosen HERE, against the path
        that actually produced the score — before this, a cosine-scale floor was
        applied to keyword-scale scores whenever the embedding server was down,
        which refused nearly every query on a degraded machine.

        ``threshold`` still overrides both, for a caller that has measured its
        own floor on a known path (the calibration harness passes 0.0 to read raw
        scores).
        """
        query = (query or "").strip()
        if not query or not self._entries:
            return None, 0.0
        if self._embeddings is not None:
            entry, score, keyword_path = self._retrieve_embedding(query)
        else:
            entry, score = self._retrieve_keyword(query)
            keyword_path = True
        if threshold is not None:
            thr = threshold
        elif keyword_path:
            thr = KEYWORD_STRONG_THRESHOLD if strong else KEYWORD_THRESHOLD
        else:
            thr = STRONG_THRESHOLD if strong else DEFAULT_THRESHOLD
        if entry is None or score < thr or not self._eligible(entry):
            return None, score
        return entry, score

    def _retrieve_embedding(self, query: str
                            ) -> "tuple[HowtoEntry | None, float, bool]":
        """The embedding match, plus whether the score actually came from the
        KEYWORD fallback — the caller needs that to pick a floor on the right
        scale when the embedding server goes away mid-session."""
        vectors = self._embedder([query]) if self._embedder else None
        if not vectors:
            # Embedding server went away after index build — fall back this turn.
            return (*self._retrieve_keyword(query), True)
        try:
            np = _np()
            q = np.asarray(vectors, dtype=np.float32)[0]
        except (ValueError, TypeError):
            return (*self._retrieve_keyword(query), True)
        np = _np()
        mat = self._embeddings
        dot = mat @ q
        sims = dot / (np.linalg.norm(mat, axis=1) * np.linalg.norm(q) + 1e-8)
        best_trig = int(np.argmax(sims))
        return (self._entries[self._trigger_owner[best_trig]],
                float(sims[best_trig]), False)

    def _retrieve_keyword(self, query: str) -> "tuple[HowtoEntry | None, float]":
        """Deterministic fallback, two measured conditions (see the floors above).

        The score is the share of the QUERY's content words that a trigger
        supplies, so a terse question fully covered by a trigger scores high and
        a question that adds context words is not punished for the addition.
        (It was a Jaccard ratio over the union until this was measured: that
        normalization refused most of the questions the corpus demonstrably
        covers, because a longer query drags the denominator up.)

        The second condition asks how much of the query the corpus's vocabulary
        accounts for at all. It is what rejects a question that reuses corpus
        words to ask about something the corpus has never heard of — the shape a
        single overlap floor cannot separate, because such a query can contain a
        real trigger outright.
        """
        q_words = self._content_words(query)
        if not q_words:
            return None, 0.0
        best_entry: HowtoEntry | None = None
        best_score = 0.0
        best_width = 0
        for t_words, owner in zip(self._trigger_words, self._trigger_owner):
            if not t_words:
                continue
            overlap = len(q_words & t_words)
            if not overlap:
                continue
            score = overlap / len(q_words)
            # Ties go to the NARROWER trigger. Query coverage alone cannot
            # choose between "how do I verify a package" and a longer trigger
            # that also happens to contain those words: both supply the whole
            # question. The trigger carrying the least beside it is the one
            # about the question and not about something larger.
            if score > best_score or (score == best_score and best_entry is not None
                                      and len(t_words) < best_width):
                best_score = score
                best_width = len(t_words)
                best_entry = self._entries[owner]
        known = len(q_words & self._vocabulary) / len(q_words)
        if known < KEYWORD_MIN_KNOWN_SHARE:
            logger.debug("howto: %r is out of the corpus's vocabulary "
                         "(known share %.2f) — refusing the keyword match",
                         query, known)
            return None, best_score
        return best_entry, best_score

    @staticmethod
    def _content_words(text: str) -> set[str]:
        return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS}
