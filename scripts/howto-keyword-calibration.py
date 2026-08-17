#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Calibrate the floors that gate the how-to corpus's KEYWORD fallback.

The curated how-to corpus (:mod:`intergen.howto`) normally retrieves by
embedding cosine. When the embedding server is unavailable — no model yet, a
cold-boot port collision, a server that stopped answering — the corpus serves a
deterministic keyword-overlap fallback instead, and that degraded path has been
observed serving on a real installed machine. Its floors were CHOSEN when the
fallback was written; this harness measures them against the real corpus so
they can be read off data instead.

Two populations, both derived from material that already exists — nothing here
is a hand-invented "expected" score:

  POSITIVE — HELD-OUT TRIGGER. Every corpus entry carries several example
  phrasings. For each entry with two or more, each trigger in turn is removed
  from the index and then asked as the query. The corpus demonstrably covers
  that question (the entry is still there, reachable through its other
  phrasings), so the fallback must SERVE something. This is a real
  generalization test: the query is never in the index it is scored against.

  NEGATIVE — OFF-CORPUS. Questions the corpus does not cover, written to share
  ordinary words with it ("how do I add a user to my gym membership" shares
  every content word of the real trigger "how do I add a user"). These must be
  REFUSED. A floor that cannot reject them is not a calibration.

  STRONG BAND. The router asks for a STRONG match in two cases: a query with no
  instructional prior ("install firefox" — an action, not a teaching request),
  and an orientation ask that names no procedure ("how do I get started"). Most
  of these must be REFUSED so the action path and the generative path
  respectively still run, but a few are questions a trigger covers word for
  word and the healthy path serves them. Which is which is not asserted here —
  it is read off what the embedding path does with the same queries.

The floors are read off those populations against one further measured
reference: what the HEALTHY path does. ``--embed`` runs every query through the
real embedding server as well, so the keyword floors can be placed where the
degraded path agrees with the embedding path it stands in for, rather than at a
number someone liked. Degrading gracefully means answering the same questions,
not answering different ones.

Usage:
    python3 <repo>/scripts/howto-keyword-calibration.py [--embed] [--json OUT]

Run it with the repository root on sys.path (cwd at the repo root); the harness
prints which ``intergen.howto`` it actually loaded, because a bare
``import intergen`` from ``scripts/`` would otherwise resolve to the INSTALLED
package and silently measure code other than this checkout's.

This lives in ``scripts/`` and NOT in ``intergen/tools/`` for the same reason
``wiki-citation-calibration.py`` does: everything under ``intergen/tools/`` is a
dispatchable assistant capability. A calibration harness is authoring tooling,
not a capability the assistant may run.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from intergen.howto import (  # noqa: E402
    KEYWORD_MIN_KNOWN_SHARE,
    KEYWORD_STRONG_THRESHOLD,
    KEYWORD_THRESHOLD,
    HowtoCorpus,
)

# ── the negative populations ──────────────────────────────────────────────
# Questions InterGenOS's teaching corpus does not answer, phrased the way a
# person actually phrases things and deliberately sharing ordinary words with
# real triggers. The last six are the hard ones: each contains a complete corpus
# trigger, or all of its distinctive words, as a subset.
OFF_CORPUS = (
    "write me a short poem about a lighthouse",
    "what is the capital of France",
    "what is a good recipe for banana bread",
    "who won the football game last night",
    "tell me a joke about penguins",
    "how do I train for a marathon",
    "how do I start a small business",
    "how do I find a good doctor near me",
    "what is the meaning of life",
    "how do I make a cup of coffee",
    "how do I connect with my estranged brother",
    "how do I change a flat tire",
    "how do I file my taxes this year",
    "how do I remove a splinter from my finger",
    "how do I update my resume for a job application",
    "what time does the hardware store open on Sunday",
    "how do I install a new battery in my car",
    "how do I search for a new job",
    "how do I delete my social media account",
    "how do I add a user to my gym membership",
)

# Queries the router hands to the corpus with STRONG required: a plain
# imperative (no instructional prior — an action for the tool path) and an
# orientation ask (a prior, but no procedure named). The split between the two
# lists below is MEASURED, not asserted: it is what the embedding path does
# with these same queries at its own strong floor, read off a live run.
#
# Must be REFUSED — the healthy path refuses all nine. An imperative the corpus
# does not cover word-for-word belongs to the action path, and an orientation
# ask has no curated answer at all.
STRONG_BAND_REFUSE = (
    "install firefox",
    "remove vlc",
    "restart bluetooth",
    # action shapes the router's own offer paths handle. The healthy path
    # refuses all five (cosine 0.5389-0.7400 against its 0.82 strong floor);
    # a keyword floor that served them would take "create a scripts folder"
    # away from the file-lifecycle offer, which is how the suite caught an
    # earlier draft of this floor.
    "create a scripts folder",
    "make a projects directory in my home folder",
    "install neovim for me",
    "restart sshd",
    "remove the transmission package",
    "how do I get started",
    "how do I use this",
    "how does this work",
    "where do I begin",
    "what can you do",
    "how do I learn InterGenOS",
)
# Legitimately SERVED — the healthy path serves all five (cosine 0.89-0.93),
# because a trigger covers each of them word for word. A degraded path that
# refused them would be answering different questions than the path it stands
# in for, which is the failure this calibration exists to prevent.
STRONG_BAND_SERVE = (
    "update my system",
    "list installed packages",
    "take a screenshot",
    "show me my ip address",
    "connect to wifi",
)
STRONG_BAND = STRONG_BAND_REFUSE + STRONG_BAND_SERVE

# Strong-band queries no overlap floor can separate, each with the measurement
# that says why, so this exemption can be re-derived instead of trusted. Both
# reduce to one or two of the corpus's most common words, which some trigger
# supplies in full — so they score exactly 1.0, the same score a real one-word
# question ("what is pkm", "how do I screenshot") earns. Refusing them by score
# would refuse those too. The embedding path refuses both; the keyword path
# serves them, and that gap is reported in every run rather than exempted away.
STRONG_BAND_UNSEPARABLE = {
    "how do I use this":
        "content words {use}; 'use' appears in 6 of 414 triggers, so a trigger "
        "supplies 1/1 of the query and the score is 1.0",
    "how does this work":
        "content words {does, work}; both appear in triggers (13 and 9 of 414), "
        "so a trigger supplies 2/2 and the score is 1.0",
}


EMBED_URL = "http://127.0.0.1:8081/v1/embeddings"


def _make_live_embedder():
    """The daemon's own embedding transport, batched, with a per-run cache.

    The cache is what makes the embedding reference affordable: the held-out
    population rebuilds the corpus 306 times, and without it the same 414
    trigger texts would be re-embedded on every rebuild. Each distinct text is
    sent to the real server exactly once; the corpus code that consumes the
    vectors is untouched."""
    import urllib.request

    cache: dict[str, list[float]] = {}

    def embed(texts: "list[str]") -> "list[list[float]] | None":
        missing = [t for t in dict.fromkeys(texts) if t not in cache]
        for i in range(0, len(missing), 32):
            batch = missing[i:i + 32]
            payload = json.dumps({"input": batch, "model": "embedding"}).encode()
            req = urllib.request.Request(
                EMBED_URL, data=payload,
                headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=300) as resp:
                    body = json.load(resp)
            except Exception as exc:  # noqa: BLE001
                print(f"  embedder failed on batch {i}: {type(exc).__name__}: {exc}")
                return None
            rows = sorted(body["data"], key=lambda r: r.get("index", 0))
            for text, row in zip(batch, rows):
                cache[text] = row["embedding"]
        return [cache[t] for t in texts]

    return embed


def _load_raw(data_dir: Path) -> list[dict]:
    out: list[dict] = []
    for path in sorted(data_dir.glob("*.json")):
        out.extend(json.loads(path.read_text(encoding="utf-8")))
    return out


def _held_out_corpus(raw: list[dict], entry_id: str, trigger: str,
                     tmp: Path, embedder=None) -> HowtoCorpus:
    """The real corpus minus ONE trigger — the query is scored against an index
    that has never seen it."""
    held = []
    for item in raw:
        if item["id"] == entry_id:
            item = dict(item)
            item["triggers"] = [t for t in item["triggers"] if t != trigger]
            if not item["triggers"]:
                continue
        held.append(item)
    (tmp / "held.json").write_text(json.dumps(held), encoding="utf-8")
    return HowtoCorpus(embedder=embedder, data_dir=tmp)


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.1f}%" if d else "n/a"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path, default=None,
                    help="also write the measured rows to this file")
    ap.add_argument("--embed", action="store_true",
                    help="also measure the healthy embedding path through the "
                         f"live server at {EMBED_URL}, and report where the "
                         "keyword floors agree with it")
    args = ap.parse_args()

    import tempfile

    import intergen.howto as mod
    print(f"measuring the code at {mod.__file__}")
    data_dir = Path(mod.__file__).resolve().parent / "data" / "howto"
    raw = _load_raw(data_dir)
    multi = [e for e in raw if len(e.get("triggers", ())) >= 2]
    print(f"corpus: {len(raw)} entries, "
          f"{sum(len(e['triggers']) for e in raw)} triggers, from {data_dir}")
    print(f"held-out-trigger population: {sum(len(e['triggers']) for e in multi)} "
          f"queries from the {len(multi)} entries with 2+ triggers "
          f"({len(raw) - len(multi)} single-trigger entries cannot be held out "
          f"and are NOT measured)")
    print(f"floors under test: KEYWORD_THRESHOLD={KEYWORD_THRESHOLD} "
          f"KEYWORD_MIN_KNOWN_SHARE={KEYWORD_MIN_KNOWN_SHARE} "
          f"KEYWORD_STRONG_THRESHOLD={KEYWORD_STRONG_THRESHOLD}")
    print()

    # No embedder is passed, so the corpus builds no embedding index and every
    # retrieve() below goes down the keyword path — the same code the daemon
    # runs when its embedding server is unavailable.
    positives = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for entry in multi:
            for trigger in entry["triggers"]:
                corpus = _held_out_corpus(raw, entry["id"], trigger, tmp)
                got, score = corpus.retrieve(trigger)
                raw_entry, raw_score = corpus.retrieve(trigger, threshold=0.0)
                positives.append({
                    "query": trigger,
                    "want": entry["id"],
                    "served": got is not None,
                    "served_id": got.id if got else None,
                    "score": score,
                    "top_id": raw_entry.id if raw_entry else None,
                    "correct_entry": bool(got is not None and got.id == entry["id"]),
                })

    whole = HowtoCorpus(embedder=None, data_dir=data_dir)
    negatives = []
    for query in OFF_CORPUS:
        got, score = whole.retrieve(query)
        top, top_score = whole.retrieve(query, threshold=0.0)
        negatives.append({"query": query, "served": got is not None,
                          "served_id": got.id if got else None, "score": score,
                          "would_have_served": top.id if top else None,
                          "top_score": top_score})
    strong = []
    for query in STRONG_BAND:
        got, score = whole.retrieve(query, strong=True)
        top, top_score = whole.retrieve(query, threshold=0.0)
        strong.append({"query": query, "served": got is not None,
                       "served_id": got.id if got else None, "score": score,
                       "would_have_served": top.id if top else None,
                       "top_score": top_score})

    served_p = [r for r in positives if r["served"]]
    correct_p = [r for r in positives if r["correct_entry"]]
    served_n = [r for r in negatives if r["served"]]
    served_s = [r for r in strong if r["served"]]

    print("== POSITIVES: held-out trigger, corpus demonstrably covers the question ==")
    print(f"  served   {len(served_p)}/{len(positives)}  ({_pct(len(served_p), len(positives))})")
    print(f"  refused  {len(positives) - len(served_p)}/{len(positives)}")
    scores = sorted(r["score"] for r in positives)
    print(f"  raw top-1 score: min {scores[0]:.4f}  median "
          f"{statistics.median(scores):.4f}  max {scores[-1]:.4f}")
    print(f"  of the served, the entry the held-out trigger belongs to: "
          f"{len(correct_p)}/{len(served_p)} ({_pct(len(correct_p), len(served_p))})")
    print("  the 12 lowest-scoring REFUSED positives (what the floor costs):")
    for r in sorted((r for r in positives if not r["served"]),
                    key=lambda r: -r["score"])[:12]:
        print(f"    score {r['score']:.4f}  {r['query']!r}  "
              f"(top match was {r['top_id']})")
    print()

    print("== NEGATIVES: off-corpus questions that must be refused ==")
    print(f"  served (WRONG) {len(served_n)}/{len(negatives)}")
    for r in served_n:
        print(f"    SERVED  score {r['score']:.4f}  {r['query']!r} -> {r['served_id']}")
    print("  the 6 closest calls (highest raw score, refused):")
    for r in sorted((r for r in negatives if not r["served"]),
                    key=lambda r: -r["top_score"])[:6]:
        print(f"    refused score {r['top_score']:.4f}  {r['query']!r}  "
              f"(would have served {r['would_have_served']})")
    print()

    print("== STRONG BAND: what the strong floor serves and refuses ==")
    print(f"  served {len(served_s)}/{len(strong)}  "
          f"(the healthy path serves {len(STRONG_BAND_SERVE)} of these by "
          f"measurement; the rest it refuses)")
    for r in served_s:
        expected = "expected" if r["query"] in STRONG_BAND_SERVE else "NOT expected"
        print(f"    SERVED  score {r['score']:.4f}  {r['query']!r} -> "
              f"{r['served_id']}   [{expected}]")
    print("  the 6 closest calls (highest raw score, refused):")
    for r in sorted((r for r in strong if not r["served"]),
                    key=lambda r: -r["top_score"])[:6]:
        print(f"    refused score {r['top_score']:.4f}  {r['query']!r}  "
              f"(would have served {r['would_have_served']})")
    print()

    print("== FLOOR SWEEP (raw top-1 scores, gate applied by this harness) ==")
    print("   floor   positives-served   off-corpus-served   strong-band-served")
    floors = sorted({round(x, 4) for x in
                     [r["score"] for r in positives]
                     + [r["top_score"] for r in negatives]
                     + [r["top_score"] for r in strong]
                     if x > 0})
    for f in floors:
        ps = sum(1 for r in positives if r["score"] >= f)
        ns = sum(1 for r in negatives if r["top_score"] >= f)
        ss = sum(1 for r in strong if r["top_score"] >= f)
        print(f"  {f:6.4f}   {ps:6d} ({_pct(ps, len(positives)):>6})"
              f"      {ns:6d}              {ss:6d}")
    print()

    print("== VERDICT ==")
    ok = True
    if served_n:
        ok = False
        print(f"  FAIL  {len(served_n)} off-corpus negative(s) are SERVED at the "
              f"floors under test")
    else:
        print("  every off-corpus negative is refused")
    unexpected = [r for r in strong
                  if r["served"] and r["query"] in STRONG_BAND_REFUSE
                  and r["query"] not in STRONG_BAND_UNSEPARABLE]
    missing = [r for r in strong
               if not r["served"] and r["query"] in STRONG_BAND_SERVE]
    if unexpected:
        ok = False
        print(f"  FAIL  {len(unexpected)} strong-band query(ies) the healthy path "
              f"refuses are SERVED here with no measured reason:")
        for r in unexpected:
            print(f"          {r['query']!r} -> {r['served_id']} "
                  f"(score {r['score']:.4f})")
    else:
        print("  every strong-band query the healthy path refuses is refused "
              "here too, except the ones no floor can separate:")
        for r in strong:
            if r["served"] and r["query"] in STRONG_BAND_UNSEPARABLE:
                print(f"    SERVED  {r['query']!r} -> {r['served_id']}   "
                      f"{STRONG_BAND_UNSEPARABLE[r['query']]}")
    if missing:
        ok = False
        print(f"  FAIL  {len(missing)} strong-band query(ies) the healthy path "
              f"SERVES are refused here:")
        for r in missing:
            print(f"          {r['query']!r} (score {r['score']:.4f})")
    else:
        print(f"  all {len(STRONG_BAND_SERVE)} strong-band queries the healthy "
              f"path serves are served here too")
    print(f"  positives served: {_pct(len(served_p), len(positives))} — the share "
          f"of demonstrably-covered questions the degraded path still answers")

    embed_rows: "list[dict]" = []
    if args.embed:
        print()
        print("== THE HEALTHY PATH, for reference: the same queries through the "
              "live embedding server ==")
        embedder = _make_live_embedder()
        probe = embedder(["calibration reachability probe"])
        if not probe:
            print(f"  the embedding server at {EMBED_URL} did not answer — the "
                  f"embedding reference cannot be measured on this machine")
        else:
            print(f"  embedding through {EMBED_URL}; vectors are {len(probe[0])}-dim")
            with tempfile.TemporaryDirectory() as td:
                tmp = Path(td)
                for entry in multi:
                    for trigger in entry["triggers"]:
                        corpus = _held_out_corpus(raw, entry["id"], trigger, tmp,
                                                  embedder=embedder)
                        if corpus._embeddings is None:  # noqa: SLF001
                            print("  the corpus did not build an embedding index "
                                  "— reference abandoned")
                            embed_rows = []
                            break
                        got, score = corpus.retrieve(trigger)
                        got_s, _ = corpus.retrieve(trigger, strong=True)
                        embed_rows.append({
                            "query": trigger, "want": entry["id"],
                            "served": got is not None, "score": score,
                            "served_strong": got_s is not None,
                            "correct_entry": bool(got and got.id == entry["id"]),
                        })
                    else:
                        continue
                    break
        if embed_rows:
            whole_e = HowtoCorpus(embedder=embedder, data_dir=data_dir)
            print("  the same off-corpus and strong-band queries, healthy path:")
            for label, pop, kw_rows, kw in (("off-corpus", OFF_CORPUS, negatives,
                                             False),
                                            ("strong-band", STRONG_BAND, strong,
                                             True)):
                by_q = {r["query"]: r for r in kw_rows}
                served_e, disagree = [], []
                for q in pop:
                    got, sc = whole_e.retrieve(q, strong=kw)
                    if got is not None:
                        served_e.append((q, got.id, sc))
                    if (got is not None) != by_q[q]["served"]:
                        disagree.append((q, got.id if got else None,
                                         by_q[q]["served_id"]))
                print(f"    {label}: embedding path serves {len(served_e)}/{len(pop)}")
                for q, eid, sc in served_e:
                    print(f"      SERVED  cosine {sc:.4f}  {q!r} -> {eid}")
                for q, e_id, k_id in disagree:
                    print(f"      DISAGREES  {q!r}  embedding={e_id}  keyword={k_id}")
            es = sum(1 for r in embed_rows if r["served"])
            ec = sum(1 for r in embed_rows if r["correct_entry"])
            print(f"  embedding path on the same {len(embed_rows)} positives: "
                  f"served {es} ({_pct(es, len(embed_rows))}), "
                  f"entry the trigger belongs to {ec} ({_pct(ec, es)})")
            by_query = {r["query"]: r for r in embed_rows}
            agree = sum(1 for r in positives
                        if r["query"] in by_query
                        and by_query[r["query"]]["served"] == r["served"])
            print(f"  keyword path AGREES with it on {agree}/{len(embed_rows)} "
                  f"({_pct(agree, len(embed_rows))}) of the served/refused verdicts")
            print("  agreement by candidate keyword floor "
                  "(vocabulary condition still applied):")
            print("     floor   agree   keyword-served   embedding-served")
            cand = sorted({round(r["score"], 4) for r in positives if r["score"] > 0})
            for f in cand:
                ag = sum(1 for r in positives
                         if r["query"] in by_query
                         and by_query[r["query"]]["served"] == (
                             r["served"] if r["score"] >= f else False)
                         and (r["score"] >= f) == (r["served"] or r["score"] >= f))
                ks = sum(1 for r in positives if r["score"] >= f and r["served"])
                ag = sum(1 for r in positives
                         if r["query"] in by_query
                         and by_query[r["query"]]["served"] == (
                             r["served"] and r["score"] >= f))
                print(f"    {f:6.4f}  {ag:6d}   {ks:14d}   {es:16d}")
            ess = sum(1 for r in embed_rows if r["served_strong"])
            print(f"  STRONG floor: the embedding path serves {ess}/{len(embed_rows)} "
                  f"positives at its own strong floor; candidate keyword strong "
                  f"floors, by agreement with that:")
            print("     floor   agree   keyword-served   embedding-served")
            for f in cand:
                ag = sum(1 for r in positives
                         if r["query"] in by_query
                         and by_query[r["query"]]["served_strong"] == (
                             r["served"] and r["score"] >= f))
                ks = sum(1 for r in positives if r["score"] >= f and r["served"])
                print(f"    {f:6.4f}  {ag:6d}   {ks:14d}   {ess:16d}")

    if args.json:
        args.json.write_text(json.dumps(
            {"positives": positives, "off_corpus": negatives,
             "strong_band": strong, "embedding_reference": embed_rows,
             "floors": {"KEYWORD_THRESHOLD": KEYWORD_THRESHOLD,
                        "KEYWORD_MIN_KNOWN_SHARE": KEYWORD_MIN_KNOWN_SHARE,
                        "KEYWORD_STRONG_THRESHOLD": KEYWORD_STRONG_THRESHOLD}},
            indent=1, sort_keys=True), encoding="utf-8")
        print(f"  rows written to {args.json}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
