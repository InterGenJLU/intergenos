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

import hashlib
import json
import logging
import os
import re
import stat
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
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

# STARTUP EMBEDDING. The installed wiki is large — 2 116 passages from 87 pages
# on a shipped system — and the embedding server it is embedded against is
# started with --parallel 1 and reached through a client with a 30 s timeout.
# Sending the corpus as one request therefore fails every time, and fails
# expensively: the client gives up while the server keeps working through the
# batch, so the single slot is still occupied when the web server begins
# accepting turns, and the first thing a user asks queues behind an answer
# nobody is waiting for any more.
#
# So the corpus goes in bounded requests, and the work is bounded in time as
# well as in size: startup belongs to the daemon, and an index that cannot be
# finished now can be finished later rather than degrading to keyword matching
# until the machine is rebooted.
_EMBED_BATCH = 32          # passages per request to the embedding server

# The on-disk index cache. The computed passage vectors are written under
# the daemon's own state directory once the whole corpus is embedded, and
# loaded at the next start when — and only when — everything they were
# computed from is unchanged: the VERIFIED page hashes of the signed manifest
# (a page that changed, or a manifest that no longer verifies, changes the
# key), the embedding model's identity (another model's vectors are not
# comparable), and this format version (bumped when the chunker or the file
# layout changes). The header carries the key, the chunk count and the
# sha256 of the vectors file; a cache whose key, count or hash does not match
# is never loaded, and nothing world-writable is read. A start that finds no
# usable cache embeds exactly as before and writes a fresh one.
_INDEX_FORMAT_VERSION = 1
_CACHE_HEADER_NAME = "index.json"
_CACHE_VECTORS_NAME = "index.npy"
_STARTUP_EMBED_BUDGET_S = 10.0   # how long index construction may spend embedding
_RESUME_EMBED_BUDGET_S = 2.0     # how long one resume_embedding() pass may spend

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
# HOW MUCH OF ONE PAGE THIS MODULE WILL READ, AND PRODUCE.
# Until 2026-09-22 there was no answer to that question in the code: every
# verified page went into the extraction whole and came out whole, so the
# largest input was whatever the largest installed page happened to be. The
# wiki ships one page that is the entire book rendered as a single document,
# so that size grows with the wiki itself. Measured on the installed wiki on
# 2026-09-22: 88 verified pages, the largest 994,504 characters of markup
# giving 734,392 characters of text. The ceilings below are more than eight and
# more than five times those figures, so nothing shipped today is cut. A page
# that grows past one keeps only text that ends on a whole word, and the cut is
# LOGGED with the page, its size, the limit and the length kept; a page with no
# word boundary under a ceiling keeps none of its text rather than part of a
# word. A page silently losing its tail would make the retrieval quietly less
# complete, and half a word would put a word the page does not contain into the
# index: those are the two failures this is written to avoid.
_MAX_PAGE_HTML_CHARS = 8_000_000
_MAX_PAGE_TEXT_CHARS = 4_000_000
# Read after cut markup so that a comment or a tag still open at the cut ends
# there: the end of a comment, both quote marks (for a quoted attribute value)
# and the end of a tag. See _text_read_before_the_cut.
_CLOSING_MARKUP = "-->\"'>"

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
    first, no-new-runtime-dep path.

    Given ``cut``, the markup read before a cut, it also records the last
    construct that begins before the cut: where it begins, whether it is text,
    and how many parts had been collected before it (see
    _text_read_before_the_cut)."""

    def __init__(self, cut: "str | None" = None) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._has_main = False
        self._in_main_depth = 0
        self._parts: list[str] = []
        # The cut as the parser counts positions: (line, column), lines from 1.
        self._cut_at: "tuple[int, int] | None" = None
        if cut is not None:
            self._cut_at = (cut.count("\n") + 1,
                            len(cut) - (cut.rfind("\n") + 1))
        self.last_before_cut: "tuple[tuple[int, int], bool, int] | None" = None

    def _begin(self, is_text: bool) -> None:
        """Note a construct beginning, when a cut is being watched for.

        The parser's position during a handler is where that construct
        begins. A start-and-end tag such as ``<br/>`` reaches two handlers at
        one position; the first is kept."""
        if self._cut_at is None:
            return
        at = self.getpos()
        if at < self._cut_at and (self.last_before_cut is None
                                  or at > self.last_before_cut[0]):
            self.last_before_cut = (at, is_text, len(self._parts))

    def handle_comment(self, data: str) -> None:
        self._begin(False)

    def handle_decl(self, decl: str) -> None:
        self._begin(False)

    def handle_pi(self, data: str) -> None:
        self._begin(False)

    def unknown_decl(self, data: str) -> None:
        self._begin(False)

    def handle_starttag(self, tag: str, attrs: object) -> None:
        self._begin(False)
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "main":
            self._has_main = True
            self._in_main_depth += 1
        if tag in _BLOCK_TAGS:
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        self._begin(False)
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "main" and self._in_main_depth:
            self._in_main_depth -= 1
        if tag in _BLOCK_TAGS:
            self._parts.append(" ")

    def handle_data(self, data: str) -> None:
        self._begin(True)
        if self._skip_depth:
            return
        # Once a <main> has been seen, only its text counts (drop the sidebar).
        if self._has_main and self._in_main_depth == 0:
            return
        self._parts.append(data)

    def text(self, parts: "int | None" = None) -> str:
        r"""The collected parts as one line of text, whitespace collapsed;
        with ``parts``, only that many of them, from the first.

        ``str.split()`` with no argument splits on runs of whitespace and drops
        the leading and trailing runs, which is character for character what
        ``re.sub(r"\s+", " ", ...).strip()`` produced — proven over every
        Unicode code point in the tests, so this is not a behaviour change.

        The collapse is done with string methods, not with a regular-expression
        substitution, on purpose. On 2026-09-20 a full test run on one of this
        project's machines ended in a segmentation fault inside that
        substitution, running over a whole rendered page; it happened once in
        about twenty runs and has not been reproduced since, so it cannot be
        shown on demand and cannot be shown fixed. What can be done is to stop
        running a substitution over a whole document for work the interpreter's
        own string code does: a substitution that is never called cannot fault.
        This method calls nothing in the regular-expression engine. The
        standard HTML tokenizer this class is built on still does, to find tags,
        attributes and character references while a page is fed in."""
        return " ".join("".join(self._parts[:parts]).split())


def _cut_on_a_word_boundary(text: str, limit: int) -> str:
    """``text`` cut to at most ``limit`` characters, ending on a whole word.

    ``text`` is the extractor's output: words separated by single spaces. A
    cut in the middle of a word would put a word the page does not contain
    into the retrieval index, where it could be matched and then quoted back as
    the page's own wording. So the text up to the limit is kept when the
    character just past the limit is a space (the limit falls at the end of a
    word), and otherwise the cut goes back to the last space inside the limit.
    Text with no space inside the limit keeps NOTHING: its first word runs past
    the limit, and part of a word is never returned."""
    if len(text) <= limit:
        return text
    if text[limit] == " ":
        return text[:limit]
    space = text.rfind(" ", 0, limit)
    return text[:space] if space > 0 else ""


def _drop_the_last_word(text: str) -> str:
    """``text`` without its last word. Used only after the MARKUP was cut.

    ``text`` is what _text_read_before_the_cut kept: text of the page's own
    constructs, in the page's order, the last of which may run past the cut.
    Its last word may therefore continue past the cut, either in the same run
    of text or after an inline tag: ``abc<b>def</b>`` cut after its ``d``
    gives ``abcd``, a word the page does not contain.
    Whether the last word continues cannot be told from the text, so it is
    dropped. Every word before it is followed by whitespace that the whole
    page's text has at the same place, so every word kept is a whole word of
    the page, in the page's order."""
    space = text.rfind(" ")
    return text[:space] if space > 0 else ""


def _read(parser: _WikiTextExtractor, markup: str, *,
          close: bool) -> _WikiTextExtractor:
    """``parser`` after being fed ``markup``, and closed when ``close`` is
    true. Never raises: a malformed page yields whatever text parsed."""
    try:
        parser.feed(markup)
        if close:
            parser.close()
    except Exception:  # noqa: BLE001 — a broken page must not take retrieval down
        logger.debug("wiki-retrieval: HTML parse degraded; using partial text",
                     exc_info=True)
    return parser


def _text_read_before_the_cut(markup: str) -> str:
    """The text of ``markup``, the first part of a page cut at the markup
    ceiling, keeping only whole words of the page's text, in its order.

    WHAT THIS RESTS ON, measured on the standard tokenizer of Python 3.14.3.
    The tokenizer ends a comment at the first ``-->`` or ``--!>`` after it,
    and a tag at the first ``>`` outside its quoted attribute values; when
    that closing markup is inside the markup read, the construct ends in the
    same place in the whole page. Two constructs end differently when it is
    not. A comment that opens as ``<!-->`` or ``<!--->`` is ended at once when
    no ``-->`` follows it, and what follows is read as text; in a whole page
    that has a later ``-->``, the comment runs to it. A quoted attribute value
    still open at the cut, with whitespace or a quote mark just before its
    equals sign or whitespace just after it, lets the tag end at a ``>``
    inside the value, and the rest of the value is read as text; in the whole
    page, the value runs to its closing quote.

    So the markup is first read with ``_CLOSING_MARKUP`` after it, and the
    last construct that begins before the cut is recorded. With those
    characters present, no construct that ends inside the markup read can end
    in either of the two ways above: a comment always has a later ``-->``, and
    a quoted value always has its closing quote. Every construct before the
    recorded one ended inside the markup read, where the whole page ends it.
    If the recorded construct is text, the markup is read again on its own and
    its text is kept up to the cut; otherwise the text collected before that
    construct is kept, and the construct and everything after it are left
    out. The last word is then dropped, because it may continue past the cut
    (see _drop_the_last_word).

    Nothing is closed here: the end of the markup read is the cut, not the end
    of the page, so what the tokenizer is holding at the cut is never passed
    on as text."""
    probe = _read(_WikiTextExtractor(cut=markup), markup + _CLOSING_MARKUP,
                  close=False)
    last = probe.last_before_cut
    if last is None:
        return ""
    _, is_text, parts = last
    if is_text:
        text = _read(_WikiTextExtractor(), markup, close=False).text()
    else:
        text = probe.text(parts)
    return _drop_the_last_word(text)


def _kept(length: int) -> str:
    """The end of a ceiling log line: how much of the page's text was kept."""
    if length:
        return f"{length} characters of text, ending on a whole word"
    return ("0 characters of text: no word before the cut is known to be "
            "whole, and part of a word is never indexed")


def html_to_text(html: str, *, source: str = "") -> str:
    """Visible body text of a rendered wiki page (chrome stripped). Never raises —
    a malformed page yields whatever text parsed, never a daemon crash.

    BOUNDED, AND NEVER SILENTLY SO. At most ``_MAX_PAGE_HTML_CHARS`` of markup
    is read and at most ``_MAX_PAGE_TEXT_CHARS`` of text is returned, and what
    is returned is the first words of the page's text, each one whole, in the
    page's order:

    * markup over its ceiling is cut at the ceiling. Markup still open at the
      cut, such as a comment or a tag, is left out with everything after it;
      text still open at the cut is kept up to it; the last word is then
      dropped, because it may continue past the cut
      (_text_read_before_the_cut says what this rests on);
    * text over its ceiling is cut back to a word boundary inside it.

    A page with no word boundary under a ceiling keeps no text at all. Each cut
    is logged, after every cut has been made, with the page, its size, the
    limit and the length kept. ``source`` is the page name for those log lines
    and is only ever used to say which page was cut.

    The bound lives HERE, in the one function every caller goes through, rather
    than at any call site: a limit applied at one caller is not a limit on the
    function."""
    html = html or ""
    page = source or "(unnamed)"
    markup_size = len(html)
    markup_cut = markup_size > _MAX_PAGE_HTML_CHARS
    if markup_cut:
        text = _text_read_before_the_cut(html[:_MAX_PAGE_HTML_CHARS])
    else:
        # close() tells the parser that its input has ended, and it then
        # finishes what it was holding back: a run of text that could end in
        # part of a character reference, a lone "<", a "</" with nothing after
        # it and the unfinished content of an element such as a script are
        # passed on as text; a comment, declaration or processing instruction
        # left open is passed on as one; a tag left part-way is dropped. A
        # whole page is closed, because its last text can be held back until
        # then. A cut page is never closed (see _text_read_before_the_cut).
        text = _read(_WikiTextExtractor(), html, close=True).text()
    text_size = len(text)
    text_cut = text_size > _MAX_PAGE_TEXT_CHARS
    if text_cut:
        text = _cut_on_a_word_boundary(text, _MAX_PAGE_TEXT_CHARS)
    # Logged after every cut, so each line states the length actually kept.
    if markup_cut:
        logger.warning(
            "wiki-retrieval: page %s is %d characters of markup, over the "
            "%d-character ceiling; read the first %d and kept %s",
            page, markup_size, _MAX_PAGE_HTML_CHARS, _MAX_PAGE_HTML_CHARS,
            _kept(len(text)))
    if text_cut:
        logger.warning(
            "wiki-retrieval: page %s produced %d characters of text, over the "
            "%d-character ceiling; kept %s",
            page, text_size, _MAX_PAGE_TEXT_CHARS, _kept(len(text)))
    return text


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
        *,
        cache_dir: "str | os.PathLike[str] | None" = None,
        embedder_identity: str = "",
    ) -> None:
        self._citations = citations
        self._embedder = embedder
        # The on-disk cache (see _INDEX_FORMAT_VERSION): both must be given
        # for a cache to exist — the directory the daemon owns and the
        # identity of the model whose vectors these are.
        self._cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._embedder_identity = str(embedder_identity or "")
        self._chunks: list[WikiChunk] = []
        self._embeddings: "np.ndarray | None" = None
        # Partial embedding progress. _vectors holds one row per chunk already
        # embedded, in chunk order, so a pass that runs out of budget or meets a
        # server that is not up can be continued from where it stopped instead
        # of starting the whole corpus again.
        self._vectors: list[list[float]] = []
        self._embed_failed_logged = False
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
            text = html_to_text(html, source=rel_html)
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
        if self._chunks and self._load_cache():
            return
        self._embed_chunks()

    def _embed_chunks(self) -> None:
        """Embed as much of the corpus as the startup budget allows."""
        self._embed_pass(_STARTUP_EMBED_BUDGET_S, at_startup=True)

    # ── the on-disk index cache ────────────────────────────────────────────

    def _cache_key(self) -> "str | None":
        """The key everything in the cache was computed from, or None when no
        cache applies (no verified pages, no embedder identity)."""
        pages = self._citations.page_hashes()
        if not pages or not self._embedder_identity:
            return None
        material = json.dumps({
            "format": _INDEX_FORMAT_VERSION,
            "chunk_words": _CHUNK_WORDS,
            "chunk_overlap": _CHUNK_OVERLAP,
            "embedder": self._embedder_identity,
            "pages": sorted(pages.items()),
        }, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _writable_by_others(path: Path) -> bool:
        """True when ``path`` is group- or world-writable, a symbolic link, or
        cannot be inspected — none of which is read as the daemon's own."""
        try:
            st = os.lstat(path)
        except OSError:
            return True
        if stat.S_ISLNK(st.st_mode):
            return True
        return bool(st.st_mode & (stat.S_IWGRP | stat.S_IWOTH))

    def _load_cache(self) -> bool:
        """Load the vectors from the cache when its key, chunk count and file
        hash all match; otherwise say why at INFO and return False so the
        corpus is embedded from scratch. Nothing world-writable is read."""
        if self._cache_dir is None:
            return False
        key = self._cache_key()
        if key is None:
            return False
        header_path = self._cache_dir / _CACHE_HEADER_NAME
        vectors_path = self._cache_dir / _CACHE_VECTORS_NAME
        if not header_path.exists() or not vectors_path.exists():
            logger.info("wiki-retrieval: no index cache under %s; embedding "
                        "the corpus", self._cache_dir)
            return False
        for path in (self._cache_dir, header_path, vectors_path):
            if self._writable_by_others(path):
                logger.info("wiki-retrieval: index cache not read: %s is "
                            "writable by others (or a link); embedding the "
                            "corpus", path)
                return False
        try:
            header = json.loads(header_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.info("wiki-retrieval: index cache not read: header "
                        "unreadable (%s); embedding the corpus",
                        type(exc).__name__)
            return False
        if not isinstance(header, dict) or header.get("key") != key:
            logger.info("wiki-retrieval: index cache not read: key mismatch "
                        "(the verified pages, the embedding model or the index "
                        "format changed); embedding the corpus")
            return False
        if header.get("chunks") != len(self._chunks):
            logger.info("wiki-retrieval: index cache not read: chunk count "
                        "%s != %d; embedding the corpus", header.get("chunks"),
                        len(self._chunks))
            return False
        try:
            raw = vectors_path.read_bytes()
        except OSError as exc:
            logger.info("wiki-retrieval: index cache not read: vectors "
                        "unreadable (%s); embedding the corpus",
                        type(exc).__name__)
            return False
        digest = hashlib.sha256(raw).hexdigest()
        if header.get("vectors_sha256") != digest:
            logger.info("wiki-retrieval: index cache not read: vectors file "
                        "hash mismatch; embedding the corpus")
            return False
        try:
            import io
            np = _np()
            arr = np.load(io.BytesIO(raw), allow_pickle=False)
            if arr.ndim != 2 or arr.shape[0] != len(self._chunks):
                raise ValueError("shape mismatch")
            arr = np.asarray(arr, dtype=np.float32)
        except Exception as exc:  # noqa: BLE001 — a bad cache is never fatal
            logger.info("wiki-retrieval: index cache not read: vectors "
                        "malformed (%s); embedding the corpus",
                        type(exc).__name__)
            return False
        self._vectors = arr.tolist()
        self._embeddings = arr
        logger.info("wiki-retrieval: index loaded from the on-disk cache "
                    "(%d passages, key %s…); no embedding request made",
                    arr.shape[0], key[:12])
        return True

    def _save_cache(self) -> None:
        """Write the completed vectors and their header under the cache
        directory, owner-only, vectors first so the header's hash always
        names a file that exists. Best-effort: a write failure is logged and
        the in-memory index is unaffected."""
        if self._cache_dir is None or self._embeddings is None:
            return
        key = self._cache_key()
        if key is None:
            return
        try:
            import io
            from intergen.private_state import private_dir, private_open
            np = _np()
            private_dir(self._cache_dir)
            # private_dir tightens only inside the daemon's owned trees; the
            # cache is owner-only wherever it is asked to live.
            if not os.path.islink(self._cache_dir):
                os.chmod(self._cache_dir, 0o700)
            buf = io.BytesIO()
            np.save(buf, self._embeddings, allow_pickle=False)
            raw = buf.getvalue()
            vectors_path = self._cache_dir / _CACHE_VECTORS_NAME
            header_path = self._cache_dir / _CACHE_HEADER_NAME
            tmp_v = vectors_path.with_suffix(".npy.tmp")
            with private_open(tmp_v, "wb") as fh:
                fh.write(raw)
            os.replace(tmp_v, vectors_path)
            header = {
                "format": _INDEX_FORMAT_VERSION,
                "key": key,
                "embedder": self._embedder_identity,
                "chunks": len(self._chunks),
                "vectors_sha256": hashlib.sha256(raw).hexdigest(),
                "written_at": time.time(),
            }
            tmp_h = header_path.with_suffix(".json.tmp")
            with private_open(tmp_h, "w", encoding="utf-8") as fh:
                json.dump(header, fh, sort_keys=True)
            os.replace(tmp_h, header_path)
            logger.info("wiki-retrieval: index cache written under %s "
                        "(%d passages, key %s…)", self._cache_dir,
                        len(self._chunks), key[:12])
        except Exception as exc:  # noqa: BLE001 — the cache is an optimisation
            logger.warning("wiki-retrieval: index cache not written (%s); the "
                           "next start embeds the corpus again",
                           type(exc).__name__)

    def resume_embedding(self) -> bool:
        """Continue embedding an index that started degraded. Returns True once
        the whole corpus is embedded.

        Safe to call repeatedly and cheap to call when there is nothing to do:
        a fully embedded index sends no request at all. That matters — the
        embedding server has one slot, and a recovery path that re-embedded on
        every call would compete with live turns for it.
        """
        if self.embeddings_ready:
            return True
        self._embed_pass(_RESUME_EMBED_BUDGET_S, at_startup=False)
        return self.embeddings_ready

    @property
    def embeddings_ready(self) -> bool:
        """True when EVERY passage is embedded and the embedding retrieval path
        is live. A partially embedded index still answers by keyword, so that a
        query is never scored against a corpus that is only half present."""
        return self._embeddings is not None

    def _embed_pass(self, budget_s: float, *, at_startup: bool) -> None:
        """Embed chunks in bounded requests until the corpus is done, the budget
        is spent, or the embedding server stops answering.

        Progress is kept either way. The first request of a pass is always
        attempted, so a small wiki still embeds in exactly one call however
        tight the budget is."""
        if not self._chunks or self._embedder is None:
            return
        began = time.monotonic()
        total = len(self._chunks)
        while len(self._vectors) < total:
            if self._vectors and (time.monotonic() - began) >= budget_s:
                logger.info(
                    "wiki-retrieval: embedded %d of %d passage(s) within the "
                    "%.1fs budget; the rest are pending and the keyword path "
                    "serves until they are embedded",
                    len(self._vectors), total, budget_s)
                return
            start = len(self._vectors)
            batch = [c.text for c in self._chunks[start:start + _EMBED_BATCH]]
            vectors = self._embedder(batch)
            if not vectors or len(vectors) != len(batch):
                # The server is down, or answered something unusable. Keep what
                # is already embedded and stop; resume_embedding() picks it up.
                if not self._embed_failed_logged:
                    self._embed_failed_logged = True
                    logger.warning(
                        "wiki-retrieval: embedding request for passages %d-%d "
                        "returned nothing usable; %d of %d embedded so far, "
                        "keyword fallback serves until the rest are embedded",
                        start, start + len(batch) - 1, len(self._vectors),
                        total)
                return
            self._vectors.extend(vectors)
        self._finalise_embeddings()

    def _finalise_embeddings(self) -> None:
        """Publish the completed vectors as the matrix retrieve() scores against."""
        try:
            np = _np()
            arr = np.asarray(self._vectors, dtype=np.float32)
            if arr.ndim != 2 or arr.shape[0] != len(self._chunks):
                raise ValueError("shape mismatch")
            self._embeddings = arr
        except (ValueError, TypeError) as exc:
            logger.warning("wiki-retrieval: malformed chunk embeddings (%s); "
                           "keyword fallback only", type(exc).__name__)
            self._embeddings = None
            self._vectors = []
            return
        self._save_cache()

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
