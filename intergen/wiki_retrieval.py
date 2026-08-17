# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Free-form retrieval over the installed wiki — grounded, cited, fail-closed.

The curated teaching corpus (:mod:`intergen.howto`) answers the high-frequency,
high-risk how-tos with hand-verified answers. This module covers the LONG TAIL:
when no curated ``HowtoEntry`` matches, InterGen searches the INSTALLED wiki
(the ``intergenos-wiki`` package payload), retrieves the best-matching page
passage, and hands it back so the freeform answer is grounded in — and CITES —
real documentation instead of the 2B improvising from its weights.

SECURITY — the retrieval NEVER surfaces an unverified page. Both the page
inventory AND the per-page integrity pins come from the SAME signed manifest the
citation path already trusts (:mod:`intergen.wiki_citations`), so this module is
a *reader* of that trust chain, never a second implementation of it:

  * INDEX-BUILD gate — a page enters the retrieval index only via
    ``WikiCitations.read_verified_page`` (read-once-then-hash against the pinned
    sha256). A tampered / unsigned / unreadable page is excluded from the index
    entirely, so it can neither be RETRIEVED (grounded-from) nor CITED. This is
    the strict fail-closed reading: we do not merely withhold the citation, we
    refuse to launder unverifiable bytes into InterGen's voice at all.
  * CITE-TIME re-gate — a hit is only returned with a citation after
    ``WikiCitations.cite_page`` re-verifies the page. A verification failure
    yields NO hit (honest fallback), never a fabricated reference.
  * BELOW-THRESHOLD — a weak best-match is treated as "the wiki has no answer":
    :meth:`retrieve` returns ``None``. The caller then answers without a wiki
    citation (honest no-answer) rather than dressing a guess as a sourced fact.
  * ANSWER-SUPPORT gate — a hit means the RETRIEVER judged a page relevant and
    the passage went into the prompt. It does NOT mean the ANSWER used it: the
    model may ignore the passage entirely and answer from its own weights, and
    citing the page then claims a provenance the answer does not have. So the
    caller emits the citation only when :func:`answer_used_passage` says the
    answer text demonstrably draws on the passage; an answer that consulted
    nothing carries no Source block at all. Origin: a locally-served poem
    request that arrived with a citation to a provider-setup page it never read.

Retrieval reuses the howto machinery (RAG over the SAME injected ``nomic-embed``
callable, with a deterministic keyword-overlap fallback when the embedder is
down) so the daemon degrades gracefully and never goes dark. With no verified
manifest installed (dev/from-source box) the index is empty and every query
returns ``None`` — the feature is simply off, exactly like citations.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:  # numpy is an embedding-path-only dep (kept out of import)
    import numpy as np

    from intergen.wiki_citations import WikiCitations

logger = logging.getLogger(__name__)


def _np():
    """Import numpy lazily (embedding path only), mirroring intergen.howto /
    intergen.semantic — the module stays importable on a first-run box before the
    AI runtime deps land; retrieval degrades to the keyword fallback."""
    import numpy as np
    return np


# A retrieval below this cosine is treated as "the wiki has no answer" — the
# caller then answers without a wiki citation (honest no-answer) rather than
# citing a weakly-related page. Wiki page CHUNKS are longer and noisier than the
# howto corpus's short trigger phrasings, so nomic cosines to them run lower than
# howto's 0.72 paraphrase floor; 0.62 keeps genuinely on-topic passages while
# rejecting tangential ones. Conservative by design (higher = fewer weak
# citations) and overridable per call — Phase-2 calibration tunes it against the
# demand corpus with the judge in the loop.
DEFAULT_THRESHOLD = 0.62
# The keyword fallback (embedder unavailable) is a coarser signal; require a
# stronger overlap before grounding a freeform answer off it.
KEYWORD_THRESHOLD = 0.45

# A retrieval hit puts a wiki passage in front of the model. Whether the ANSWER
# then used it is a separate question, and the citation is a claim about the
# answer, not about the retrieval. These two floors decide when that claim may be
# made; see :func:`answer_used_passage`.
#
# CALIBRATION (measured 2026-08-06 against the shipped 87-page book, 2093 indexed
# passages; harness: intergen/tools/wiki_citation_calibration.py):
#   * The case this gate exists for — a request for a short poem, answered from
#     the model's own weights, measured against the page it cited — shares
#     exactly ONE word with that page ("through"), for a support of 0.1667. Note
#     what that says: the FRACTION alone would not have rejected it. The
#     shared-word floor is what does, and it is the floor the rule needs.
#   * A lower-bound positive population: the 29 curated how-to answers whose
#     pinned wiki page is in the shipped book, each measured against the passage
#     retrieval returns for that page. These UNDERSTATE a grounded answer,
#     because a curated answer was authored independently and only has to AGREE
#     with the page, whereas a model answering from an injected passage tracks
#     its wording. They span 0.05-0.47, median 0.17, with 2-23 shared words.
# So MIN_SHARED_WORDS carries the rejection the rule actually asks for — it sits
# above the negative's 1 shared word and at the bottom of the positive
# population's 4-to-23 range — and MIN_ANSWER_SUPPORT sits BELOW that
# population's median, where it only rejects the pathological shape a word floor
# alone would miss: a long answer touching the passage in passing.
#
# WHAT THIS CANNOT DO, stated rather than implied: text overlap cannot tell
# "used the passage" apart from "independently knew the same material". It
# reliably rejects an answer with no connection to the page it would cite, which
# is the false-provenance case; it does not certify that a passing answer was
# caused by the passage.
MIN_ANSWER_SUPPORT = 0.15
MIN_SHARED_WORDS = 4

# Chunking: wiki pages are whole documents, so they are split into overlapping
# word windows for retrieval recall (a query usually matches ONE section, not the
# whole page). ~140 words ≈ a section/paragraph group; a 30-word overlap keeps a
# concept that straddles a boundary findable from either side.
_CHUNK_WORDS = 140
_CHUNK_OVERLAP = 30

_WORD_RE = re.compile(r"[a-z0-9]+")
# Same retrieval-noise stopwords as intergen.howto (kept local — the two indexes
# are independent and may diverge; a shared list would couple them).
_STOPWORDS = frozenset((
    "how", "do", "i", "to", "the", "a", "an", "my", "is", "what", "whats",
    "can", "you", "me", "of", "on", "in", "for", "and", "it", "this", "that",
    "with", "show", "tell", "explain", "command", "s", "are", "does", "where",
))

# mdBook renders the page body inside <main>; the sidebar nav, page header, and
# scripts are chrome we must NOT index (retrieving the nav would match every
# query). Content inside these tags is dropped wholesale.
_SKIP_TAGS = frozenset(("script", "style", "nav", "header", "footer", "head"))
# Block-level tags whose boundaries should become whitespace so words on either
# side do not fuse ("...disk</li><li>Encryption..." -> two words, not one).
_BLOCK_TAGS = frozenset((
    "p", "div", "li", "ul", "ol", "br", "h1", "h2", "h3", "h4", "h5", "h6",
    "tr", "td", "th", "section", "article", "pre", "code", "table", "main",
))


class _WikiTextExtractor(HTMLParser):
    """Extract the visible body text of a rendered wiki page.

    Prefers the ``<main>`` region (mdBook's article body) when present so the
    sidebar navigation is excluded; falls back to all non-chrome text on a page
    with no ``<main>``. Dependency-free (stdlib ``html.parser``) — the mirror-
    first, no-new-runtime-dep path."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._has_main = False
        self._in_main_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "main":
            self._has_main = True
            self._in_main_depth += 1
        if tag in _BLOCK_TAGS:
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "main" and self._in_main_depth:
            self._in_main_depth -= 1
        if tag in _BLOCK_TAGS:
            self._parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        # Once a <main> has been seen, only its text counts (drop the sidebar).
        if self._has_main and self._in_main_depth == 0:
            return
        self._parts.append(data)

    def text(self) -> str:
        return re.sub(r"\s+", " ", "".join(self._parts)).strip()


def html_to_text(html: str) -> str:
    """Visible body text of a rendered wiki page (chrome stripped). Never raises —
    a malformed page yields whatever text parsed, never a daemon crash."""
    parser = _WikiTextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 — a broken page must not take retrieval down
        logger.debug("wiki-retrieval: HTML parse degraded; using partial text",
                     exc_info=True)
    return parser.text()


def _chunk_words(text: str, size: int = _CHUNK_WORDS,
                 overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """Split page text into overlapping word windows. A short page is one chunk."""
    words = text.split()
    if not words:
        return []
    if len(words) <= size:
        return [text]
    step = max(1, size - overlap)
    return [" ".join(words[i:i + size]) for i in range(0, len(words), step)
            if words[i:i + size]]


def _content_words(text: str) -> "set[str]":
    """The distinctive words of a text: lowercased alphanumeric tokens minus the
    retrieval-noise stopwords."""
    return {w for w in _WORD_RE.findall((text or "").lower())
            if w not in _STOPWORDS}


def answer_support(answer: str, passage: str, query: str = "") -> float:
    """How much of ``answer`` the ``passage`` actually supplies, in [0, 1].

    The share of the answer's own distinctive words that also appear in the
    passage. Words the USER supplied in ``query`` are excluded from both the
    numerator and the denominator, so an answer earns nothing for echoing the
    question back — only for material that could have come from the passage.

    Deterministic and embedder-independent on purpose: this decides whether a
    provenance claim is made, so it must give the same verdict when the
    embedding server is down as when it is up.
    """
    own = _content_words(answer) - _content_words(query)
    if not own:
        return 0.0
    return len(own & _content_words(passage)) / len(own)


def answer_used_passage(answer: str, passage: str, query: str = "", *,
                        minimum: "float | None" = None,
                        min_shared: "int | None" = None) -> bool:
    """Whether ``answer`` demonstrably drew on ``passage``.

    A retrieval hit means the RETRIEVER thought a page was relevant. It does not
    mean the ANSWER used that page: the passage is injected as grounding, and a
    model is free to ignore it entirely and answer from its own weights. Citing
    the page anyway states a provenance the answer does not have, which is a
    false claim about where the words came from — so the citation is emitted only
    when this check passes.

    Two conditions, both required, so neither a tiny answer nor a sprawling one
    can pass on coincidence alone: the fraction of the answer's own words that
    the passage supplies must reach :data:`MIN_ANSWER_SUPPORT`, AND at least
    :data:`MIN_SHARED_WORDS` distinct words must be shared.
    """
    thr = MIN_ANSWER_SUPPORT if minimum is None else minimum
    floor = MIN_SHARED_WORDS if min_shared is None else min_shared
    own = _content_words(answer) - _content_words(query)
    if not own:
        return False
    shared = own & _content_words(passage)
    return len(shared) >= floor and (len(shared) / len(own)) >= thr


@dataclass(frozen=True)
class WikiChunk:
    """One retrievable passage: which verified page it came from + its text."""

    rel_html: str
    title: str
    text: str


@dataclass(frozen=True)
class WikiHit:
    """A retrieval result: the grounding passage, its score, and a VERIFIED
    citation line (re-checked at retrieve time) for the page it came from."""

    rel_html: str
    title: str
    passage: str
    score: float
    citation: str


class WikiRetrieval:
    """Free-form retrieval over the verified installed wiki.

    Compose with a constructed :class:`~intergen.wiki_citations.WikiCitations`
    (the trust chain) and the SAME embedder callable the matcher/howto use
    (``list[str] -> list[list[float]] | None``; optional — without it the keyword
    fallback serves). The index is built ONCE at construction from the pages the
    manifest verifies; a page whose bytes do not verify is silently excluded."""

    def __init__(
        self,
        citations: "WikiCitations",
        embedder: "Callable[[list[str]], list[list[float]] | None] | None" = None,
    ) -> None:
        self._citations = citations
        self._embedder = embedder
        self._chunks: list[WikiChunk] = []
        self._embeddings: "np.ndarray | None" = None
        self._build_index()

    # ── index ────────────────────────────────────────────────────────────────

    def _build_index(self) -> None:
        """Read every VERIFIED page, extract body text, chunk it, and (if an
        embedder is available) embed the chunks. Fail-closed: a page that does not
        verify never enters the index."""
        from intergen.wiki_citations import _title_for_page

        excluded = 0
        for rel_html in sorted(self._citations.page_hashes()):
            html = self._citations.read_verified_page(rel_html)
            if html is None:
                # Not in the signed manifest / hash mismatch / unreadable — the
                # verify-then-cite gate already logged loud on tamper. Skip it.
                excluded += 1
                continue
            text = html_to_text(html)
            if not text:
                continue
            title = _title_for_page(rel_html)
            for chunk in _chunk_words(text):
                self._chunks.append(WikiChunk(rel_html, title, chunk))
        if self._chunks:
            logger.info("wiki-retrieval: indexed %d passage(s) from %d verified "
                        "page(s)", len(self._chunks),
                        len({c.rel_html for c in self._chunks}))
        elif self._citations.available:
            logger.info("wiki-retrieval: verified wiki present but no indexable "
                        "text extracted (%d page(s) excluded)", excluded)
        self._embed_chunks()

    def _embed_chunks(self) -> None:
        if not self._chunks or self._embedder is None:
            return
        vectors = self._embedder([c.text for c in self._chunks])
        if not vectors:
            logger.warning("wiki-retrieval: embedder returned nothing; keyword "
                           "fallback only")
            return
        try:
            np = _np()
            arr = np.asarray(vectors, dtype=np.float32)
            if arr.ndim != 2 or arr.shape[0] != len(self._chunks):
                raise ValueError("shape mismatch")
            self._embeddings = arr
        except (ValueError, TypeError) as exc:
            logger.warning("wiki-retrieval: malformed chunk embeddings (%s); "
                           "keyword fallback only", type(exc).__name__)
            self._embeddings = None

    @property
    def available(self) -> bool:
        """True when at least one verified page passage is indexed."""
        return bool(self._chunks)

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    # ── retrieval ──────────────────────────────────────────────────────────────

    def retrieve(self, query: str, *, threshold: "float | None" = None
                 ) -> "WikiHit | None":
        """The best verified-page passage for ``query``, or ``None``.

        ``None`` whenever: the index is empty (no verified wiki), the best score
        is below threshold (honest no-answer — the wiki does not cover this), or
        the winning page fails its cite-time re-verification (honest fallback, no
        fabricated reference). A non-``None`` hit ALWAYS carries a verified
        citation for the exact page its passage came from."""
        query = (query or "").strip()
        if not query or not self._chunks:
            return None
        if self._embeddings is not None:
            idx, score = self._retrieve_embedding(query)
            thr = DEFAULT_THRESHOLD if threshold is None else threshold
        else:
            idx, score = self._retrieve_keyword(query)
            thr = KEYWORD_THRESHOLD if threshold is None else threshold
        if idx is None or score < thr:
            return None
        chunk = self._chunks[idx]
        # CITE-TIME re-gate: the page must STILL verify before we cite it. A page
        # that was verified at index build but tampered since yields no hit.
        citation = self._citations.cite_page(chunk.rel_html, title=chunk.title)
        if citation is None:
            logger.error("wiki-retrieval: top match %s failed cite-time "
                         "verification — refusing to cite (honest fallback).",
                         chunk.rel_html)
            return None
        return WikiHit(rel_html=chunk.rel_html, title=chunk.title,
                       passage=chunk.text, score=float(score), citation=citation)

    def _retrieve_embedding(self, query: str) -> "tuple[int | None, float]":
        vectors = self._embedder([query]) if self._embedder else None
        if not vectors:
            return self._retrieve_keyword(query)  # embedder went away this turn
        try:
            np = _np()
            q = np.asarray(vectors, dtype=np.float32)[0]
        except (ValueError, TypeError):
            return self._retrieve_keyword(query)
        np = _np()
        mat = self._embeddings
        sims = (mat @ q) / (np.linalg.norm(mat, axis=1) * np.linalg.norm(q) + 1e-8)
        best = int(np.argmax(sims))
        return best, float(sims[best])

    def _retrieve_keyword(self, query: str) -> "tuple[int | None, float]":
        """Deterministic fallback: best content-word overlap between the query and
        any chunk, normalized by the query's content words so a terse query that
        is fully covered by a passage scores high."""
        q_words = self._content_words(query)
        if not q_words:
            return None, 0.0
        best_idx: "int | None" = None
        best_score = 0.0
        for i, chunk in enumerate(self._chunks):
            c_words = self._content_words(chunk.text)
            if not c_words:
                continue
            overlap = len(q_words & c_words)
            if not overlap:
                continue
            # Normalize by the QUERY's words (recall-oriented): a short query
            # whose every content word appears in the passage scores ~1.0, even
            # though the passage carries many more words than the query.
            score = overlap / len(q_words)
            if score > best_score:
                best_score = score
                best_idx = i
        return best_idx, best_score

    @staticmethod
    def _content_words(text: str) -> "set[str]":
        return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS}
