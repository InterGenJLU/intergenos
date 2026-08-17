# Free-form wiki lookup-and-cite — design note

Design half of the free-form wiki retrieval lane. Covers the retrieval index
shape, page chunking, and the no-answer threshold, plus the trust boundary and
one scoping decision surfaced for the reconcile. The build half is
`intergen/wiki_retrieval.py` + its additive surface on `intergen/wiki_citations.py`,
tested by `intergen/tests/test_wiki_retrieval.py`.

## Problem

The curated teaching corpus (`intergen/howto.py`) answers high-frequency,
high-risk how-tos with hand-verified answers. It cannot cover the long tail. When
no curated `HowtoEntry` matches, a free-form turn on the locked floor otherwise
improvises from the model's weights — the exact silent-failure surface the
teaching corpus exists to remove. The installed wiki already ships the answers;
retrieval lets a free-form answer be grounded in — and cite — that real, verified
documentation instead of a guess.

## Trust boundary (the reason this is not a search box)

Retrieval reuses the EXISTING verify-then-cite chain in `wiki_citations.py`; its
trust semantics are unchanged. The signed per-page `sha256` manifest
(`pages-manifest.json` + detached `.asc`, `gpgv`-verified against the pinned
operator key) is BOTH the page inventory to index AND the integrity pin. Two
gates, fail-closed at each:

1. **Index-build gate.** A page enters the index only through
   `WikiCitations.read_verified_page` — a read-once-then-hash against the pin (no
   verify-then-reread TOCTOU window: the bytes indexed are the bytes that
   verified). A tampered / unsigned / unreadable page is excluded from the index
   entirely, so it can neither be grounded-from NOR cited. This is the stronger
   reading of "never launder unverifiable docs through the assistant's voice":
   the answer body is protected, not just the citation.
2. **Cite-time re-gate.** A hit is returned only after `cite_page` re-verifies the
   page. A page tampered AFTER index build yields no hit — an honest fallback,
   never a fabricated reference.

With no verified manifest (a from-source box, or a present-but-unverifiable
manifest) the index is empty and every query returns `None`. The feature is
simply off, exactly like citations — never an error, never a guess.

The additive surface on `wiki_citations.py` (`page_hashes`, `verify_page`,
`read_verified_page`, `cite_page`) introduces no new trust rules; each gates on
the same signed-manifest + `sha256` check `cite()` already uses. Retrieval is a
second reader of that one chain.

## Index shape (reuse of the howto machinery)

The RAG shape mirrors `HowtoCorpus`: the SAME injected `nomic-embed` callable the
`SemanticMatcher` and howto corpus use (no new model loaded), with a
deterministic keyword-overlap fallback so the daemon degrades gracefully when the
embedding server is down and never goes dark. Built once at construction (guarded,
additive — a build failure degrades to no-wiki-grounding, never a startup risk).

Where it differs from howto, and why:

- **Unit of retrieval = a page passage, not a trigger phrase.** Howto embeds
  short authored trigger phrasings; the wiki has whole rendered pages. So a page
  is reduced to visible body text (`html.parser`, stdlib — the mirror-first,
  no-new-runtime-dep path; the `<main>` region is preferred so the sidebar nav is
  excluded) and split into overlapping word windows.
- **Chunking.** `~140`-word windows with a `~30`-word overlap (`_CHUNK_WORDS` /
  `_CHUNK_OVERLAP`). A query usually matches one section, not the whole page;
  windowing raises retrieval precision, and the overlap keeps a concept that
  straddles a boundary findable from either side. A short page is a single chunk.
- **Keyword fallback normalization** is by the QUERY's content words (recall-
  oriented): a terse query fully covered by a passage scores near `1.0` even
  though the passage carries many more words. Howto normalizes by the union
  (its trigger and query are comparable in length); a page chunk is not.

## No-answer threshold

A best-match below threshold is treated as "the wiki has no answer": `retrieve`
returns `None`. Defaults:

- Embedding cosine `DEFAULT_THRESHOLD = 0.62`. Page chunks are longer and noisier
  than howto's short trigger phrasings, so `nomic` cosines to them run below
  howto's `0.72` paraphrase floor. `0.62` keeps genuinely on-topic passages while
  rejecting tangential ones — conservative by design (higher = fewer weak
  citations).
- Keyword fallback `KEYWORD_THRESHOLD = 0.45` (a coarser signal wants a stronger
  overlap before it grounds an answer).

Both are per-call overridable. They are seed values, not final: Phase-2 calibrates
them against the demand corpus with the judge in the loop (the same
measure-then-tune discipline as the rest of the quality arc).

## Router wiring

Thin, on the P4 free-form path (`_try_llm_freeform`). When there is no curated
answer AND no installed-tool grounding facts, `_wiki_grounding` retrieves a
verified passage, injects it as the answer's grounding block, and — post-
generation, after any save offer — appends the verified citation, mirroring the
curated explain path's citation tail. The synthesis span records
`grounding_source="wiki"` so a reconstruction shows which source shaped the
answer. A below-threshold or unverifiable result grounds nothing and cites
nothing; the model answers as it otherwise would.

## Scoping surfaced for the reconcile

The dispatch describes the below-threshold experience as the assistant saying it
does not know and pointing at the wiki generally. That honest no-answer is
implemented at the retrieval boundary (`retrieve` returns `None` — no fabricated
citation, no unverified content injected). Wiring a SPOKEN "I don't know, but the
wiki is here" line as blanket free-form behavior is NOT part of this build,
deliberately: a blanket deflection would regress the wave-6 general-knowledge
teaching path ("how do I make a secure password", "back up my files"), which
correctly answers from general knowledge with no wiki page. Distinguishing a
system/wiki-domain ask from a general-knowledge ask needs a domain signal on the
free-form turn; that is a distinct piece, surfaced here for sequencing rather
than absorbed silently.
