#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Calibrate the answer-support floors that gate a free-form wiki citation.

Run against a machine with the signed wiki installed. It measures
:func:`intergen.wiki_retrieval.answer_support` over two populations drawn from
real material, so the two floors in ``wiki_retrieval`` are read off measured
data instead of chosen:

  NEGATIVE — an answer that consulted nothing. The captured case is a poem
  request: the retriever returns a page, the model writes a poem from its own
  weights, and the answer owes the page nothing.

  POSITIVE — answers that genuinely came from a wiki page. The curated how-to
  corpus supplies these: every entry's answer is pinned at authoring time to the
  wiki page named in its ``doc_source``, so where retrieval also lands on that
  same page, the pair is a real "the answer did consult this page" sample.

This lives in ``scripts/`` and NOT in ``intergen/tools/``: everything under
``intergen/tools/`` is a dispatchable assistant capability, inventoried and
gated by the capability inventory. A calibration harness is authoring tooling,
not a capability the assistant may run.

Usage:  python3 /path/to/repo/scripts/wiki-citation-calibration.py [--embed]
        (run with the repository root on sys.path, e.g. cwd at the repo root)

``--embed`` runs retrieval through the live embedding server on :8081 (the same
one the daemon uses) instead of the deterministic keyword fallback, so the
retrieval half matches what a serving box actually does. The gate itself is
embedder-independent either way.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

# Running a script out of scripts/ puts scripts/ on sys.path, not the repository
# root, so a bare `import intergen` resolves to the INSTALLED package and the
# harness would silently measure code other than this checkout's. Put the repo
# root first, and print which intergen actually loaded.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from intergen.howto import HowtoCorpus
from intergen.wiki_citations import WikiCitations, wiki_page_from_doc_source
from intergen.wiki_retrieval import (
    MIN_ANSWER_SUPPORT,
    MIN_SHARED_WORDS,
    WikiRetrieval,
    _content_words,
    answer_support,
    answer_used_passage,
)

# The captured case this gate exists because of: a locally-served request for a
# short poem, answered on the machine, that ended with a Source line pointing at
# a setup page the answer never consulted. The answer text is verbatim from the
# serving log.
POEM_QUERY = "Write a short poem about a lighthouse."
POEM_ANSWER = "A lighthouse stands tall,\nGuiding ships through the dark."
# The page that answer cited without having read it.
CITED_PAGE = "assistant/frontier-provider-setup.html"


EMBED_URL = "http://127.0.0.1:8081/v1/embeddings"


def _live_embedder(texts: "list[str]") -> "list[list[float]] | None":
    """The daemon's own embedding transport, batched so one huge request cannot
    stall the server. Returns None on any failure so retrieval degrades to its
    keyword fallback exactly as it does in the daemon."""
    out: "list[list[float]]" = []
    for i in range(0, len(texts), 32):
        payload = json.dumps({"input": texts[i:i + 32],
                              "model": "embedding"}).encode()
        req = urllib.request.Request(
            EMBED_URL, data=payload,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                body = json.load(resp)
        except Exception as exc:  # noqa: BLE001
            print(f"  embedder failed at batch {i}: {type(exc).__name__}: {exc}")
            return None
        out.extend(item["embedding"] for item in body["data"])
    return out


def main() -> int:
    import intergen.wiki_retrieval as wr_mod
    print(f"measuring the code at {wr_mod.__file__}")
    use_embedder = "--embed" in sys.argv
    citations = WikiCitations()
    if not citations.available:
        print("no verified wiki installed — calibration needs the real corpus")
        return 2
    if use_embedder:
        print(f"embedding retrieval through the live server at {EMBED_URL}")
    retrieval = WikiRetrieval(citations,
                              embedder=_live_embedder if use_embedder else None)
    print("retrieval path: "
          + ("embeddings" if retrieval._embeddings is not None  # noqa: SLF001
             else "keyword fallback"))
    print(f"corpus: {len(citations.page_hashes())} verified pages, "
          f"{retrieval.chunk_count} indexed passages")
    print(f"floors under test: MIN_ANSWER_SUPPORT={MIN_ANSWER_SUPPORT} "
          f"MIN_SHARED_WORDS={MIN_SHARED_WORDS}")
    print()

    print("== NEGATIVE: an answer that consulted nothing ==")
    # Measured against THE PAGE THAT WAS CITED, not against whatever retrieval
    # happens to return for this query. The captured defect was a citation to
    # that specific page, so that page is what the answer has to be checked
    # against; making the row depend on retrieval would let a retrieval change
    # quietly remove the negative sample.
    neg = []
    cited_page = CITED_PAGE
    page_text = citations.read_verified_page(cited_page)
    if page_text is None:
        print(f"  {cited_page} is not in this machine's verified book — the "
              f"negative sample cannot be measured here")
    else:
        from intergen.wiki_retrieval import _chunk_words, html_to_text
        q_words = _content_words(POEM_QUERY)
        chunks = _chunk_words(html_to_text(page_text)) or [""]
        best = max(chunks, key=lambda c: len(q_words & _content_words(c)))
        support = answer_support(POEM_ANSWER, best, POEM_QUERY)
        own = _content_words(POEM_ANSWER) - _content_words(POEM_QUERY)
        shared = own & _content_words(best)
        used = answer_used_passage(POEM_ANSWER, best, POEM_QUERY)
        print(f"  request:  {POEM_QUERY!r}")
        print(f"  answer:   {POEM_ANSWER!r}")
        print(f"  measured against the page that was cited: {cited_page}")
        print(f"  answer's own words: {sorted(own)}")
        print(f"  shared with the page passage: {sorted(shared)}")
        print(f"  answer_support = {support:.4f}   shared = {len(shared)}")
        print(f"  answer_used_passage -> {used}   "
              f"({'CITED' if used else 'NOT CITED'})")
        neg = [support]
    print()

    print("== POSITIVE: curated answers pinned to the page retrieval also found ==")
    corpus = HowtoCorpus()
    pages = citations.page_hashes()
    samples = []
    # The corpus exposes a count, not the list; this is a calibration tool run by
    # hand against a real corpus, so it reads the loaded entries directly.
    for entry in corpus._entries:  # noqa: SLF001
        page = wiki_page_from_doc_source(entry.doc_source)
        if page is None or page[0] not in pages:
            continue
        for trigger in entry.triggers:
            got = retrieval.retrieve(trigger)
            if got is None or got.rel_html != page[0]:
                continue
            support = answer_support(entry.answer, got.passage, trigger)
            own = _content_words(entry.answer) - _content_words(trigger)
            shared = own & _content_words(got.passage)
            used = answer_used_passage(entry.answer, got.passage, trigger)
            samples.append((support, len(shared), entry.id, page[0], used))
            break
    if not samples:
        print("  no curated entry's pinned page was also the retrieval winner")
    for support, shared, eid, page, used in sorted(samples):
        print(f"  support {support:.4f}  shared {shared:3d}  "
              f"{'CITED    ' if used else 'NOT CITED'}  {eid}  <- {page}")
    print()

    # The population above is small by construction: when a curated entry exists,
    # the curated path answers and free-form retrieval never runs, so retrieval
    # landing on the pinned page is incidental. Widen it by asking the same
    # question of the page the answer IS pinned to: give the entry's trigger to
    # that page's own chunks, take the chunk the retriever would pick there, and
    # measure the curated answer against it. That is exactly the shape the gate
    # sees on a genuinely grounded turn.
    print("== POSITIVE (wider): curated answer vs the best chunk of its pinned page ==")
    by_page: dict[str, list] = {}
    for chunk in retrieval._chunks:  # noqa: SLF001
        by_page.setdefault(chunk.rel_html, []).append(chunk)
    wide = []
    for entry in corpus._entries:  # noqa: SLF001
        page = wiki_page_from_doc_source(entry.doc_source)
        if page is None or page[0] not in by_page:
            continue
        trigger = entry.triggers[0] if entry.triggers else ""
        q_words = _content_words(trigger)
        if not q_words:
            continue
        best, best_score = None, -1.0
        for chunk in by_page[page[0]]:
            c_words = _content_words(chunk.text)
            score = len(q_words & c_words) / len(q_words) if c_words else 0.0
            if score > best_score:
                best, best_score = chunk, score
        if best is None:
            continue
        support = answer_support(entry.answer, best.text, trigger)
        own = _content_words(entry.answer) - _content_words(trigger)
        shared = own & _content_words(best.text)
        used = answer_used_passage(entry.answer, best.text, trigger)
        wide.append((support, len(shared), entry.id, page[0], used))
    for support, shared, eid, page, used in sorted(wide):
        print(f"  support {support:.4f}  shared {shared:3d}  "
              f"{'CITED    ' if used else 'NOT CITED'}  {eid}  <- {page}")
    print()

    print("== SUMMARY ==")
    if neg:
        print(f"  negative sample support:  {neg[0]:.4f}")
    for label, pop in (("retrieval-winner", samples), ("pinned-page", wide)):
        if not pop:
            continue
        vals = sorted(s[0] for s in pop)
        cited = sum(1 for s in pop if s[4])
        mid = vals[len(vals) // 2]
        print(f"  {label} positives: {len(pop)}   min {vals[0]:.4f}  "
              f"median {mid:.4f}  max {vals[-1]:.4f}")
        print(f"    shared-word counts: min {min(s[1] for s in pop)}  "
              f"max {max(s[1] for s in pop)}")
        print(f"    would be cited under the current floors: {cited}/{len(pop)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
