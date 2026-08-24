"""GATE 8 — the intent corpus and the layer that selects from it (section 9 line 7).

TWO SEPARATE PROPERTIES, DELIBERATELY IN ONE FILE, BECAUSE THEY MASK EACH OTHER.

1. THE SELECTION RULE. The shipped embedding layer decides whether an intent was
   matched inside a running-maximum loop: an intent becomes the answer only if it is
   the highest-scoring intent seen SO FAR and it clears its own threshold. Those are
   two different questions. An intent that clears its threshold but is preceded by a
   higher-scoring intent that does NOT clear its own threshold is silently dropped;
   and the score returned alongside a matched intent can belong to a different intent
   entirely. Each intent carries its own threshold, so this is not a corner case.

2. THE CORPUS. On the released system no shipped intent reaches its threshold on real
   first-time-user language at all, so the selection rule above can never be observed
   in production — the layer returns nothing regardless. That is why the selection
   defect has to be proven with a controlled probe: a gate that only swept the real
   corpus would pass while the rule stayed wrong, which is the exact shape of an
   instrument that has never been shown to detect a true positive.

The first test runs the SHIPPED matcher's SHIPPED selection code with a controlled
embedder, because the property under test is the selection rule and nothing else. The
second runs the SHIPPED corpus against the REAL embedding server this machine runs.

EXPECTED TO FAIL ON R001.1 AS SHIPPED.
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request

import pytest

EMBED_URL = "http://127.0.0.1:8081/v1/embeddings"


def _unit(cos: float) -> list[float]:
    """A 2-D unit vector whose cosine with [1, 0] is exactly ``cos``."""
    return [cos, math.sqrt(max(0.0, 1.0 - cos * cos))]


def _controlled_embedder(table: dict[str, float]):
    """An embedder whose similarity to the query is fixed by a lookup table."""
    def embed(texts):
        out = []
        for t in texts:
            if t == "QUERY":
                out.append([1.0, 0.0])
            else:
                out.append(_unit(table[t]))
        return out
    return embed


def _make_matcher(order: list[tuple[str, float, float]]):
    """Register intents in the given order: (intent_id, similarity, threshold)."""
    from intergen.semantic import SemanticMatcher
    table = {f"example of {iid}": sim for iid, sim, _ in order}
    m = SemanticMatcher(embedder=_controlled_embedder(table))
    for iid, _sim, threshold in order:
        m.register_intent(iid, [f"example of {iid}"], threshold=threshold,
                          tool_name=f"tool_{iid}")
    if m.get_intent_count() != len(order):
        pytest.fail(
            f"Registered {len(order)} intents but the matcher holds "
            f"{m.get_intent_count()}; the probe did not build what it describes.")
    return m


def test_an_intent_that_clears_its_threshold_is_returned(installed_intergen_dir):
    """A qualifying intent must be selected even when a higher, non-qualifying one exists.

    ``high`` scores 0.99 against a threshold of 0.999, so it does not qualify.
    ``low`` scores 0.95 against a threshold of 0.90, so it does. ``high`` is evaluated
    first. The property is simply: the layer returns the intent that qualifies.
    """
    m = _make_matcher([("high", 0.99, 0.999), ("low", 0.95, 0.90)])
    result = m._match_embeddings("QUERY")
    assert result.intent_id == "low", (
        "\nThe embedding layer dropped an intent that qualified.\n"
        "  intent 'low'  : similarity 0.95, its own threshold 0.90 — QUALIFIES\n"
        "  intent 'high' : similarity 0.99, its own threshold 0.999 — does not qualify\n"
        f"  returned      : intent_id={result.intent_id!r}, score={result.score!r}, "
        f"tool={getattr(result, 'tool_name', None)!r}\n"
        "The selection loop only considers an intent while it is the running maximum, "
        "and each intent carries its own threshold. An intent that qualifies but is not "
        "the top scorer is never looked at, so the user's turn falls through to the "
        "model with no tool attached."
    )


def test_the_score_returned_belongs_to_the_intent_returned(installed_intergen_dir):
    """The reported similarity must describe the intent that was matched.

    ``low`` (0.95, threshold 0.90) is evaluated first and qualifies. ``high``
    (0.99, threshold 0.999) is evaluated second, does not qualify, but raises the
    running maximum. The returned result then carries one intent's identity and
    another intent's score.
    """
    m = _make_matcher([("low", 0.95, 0.90), ("high", 0.99, 0.999)])
    result = m._match_embeddings("QUERY")
    assert not (result.intent_id == "low" and abs(result.score - 0.95) > 1e-6), (
        "\nThe embedding layer returned one intent's identity with another intent's "
        "score.\n"
        f"  returned intent_id : {result.intent_id!r}\n"
        f"  returned score     : {result.score!r}\n"
        "  'low' actually scored 0.95; 'high' scored 0.99 and did not qualify.\n"
        "Every consumer downstream — the confidence surfaced to the user, the decision "
        "trace, and any threshold tuning done from logged scores — reads that number as "
        "belonging to the matched intent."
    )


def _real_embedder(texts):
    body = json.dumps({"input": list(texts), "model": "embedding"}).encode()
    req = urllib.request.Request(EMBED_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180.0) as resp:
        data = json.loads(resp.read())
    rows = data["data"]
    return [r["embedding"] for r in sorted(rows, key=lambda r: r.get("index", 0))]


def test_the_shipped_corpus_can_reach_a_threshold_on_real_user_language(record_property):
    """Sweep every shipped intent against the real field sentences and the real embedder.

    This is the corpus half. It records, for every intent, the highest similarity it
    reached on any field sentence and the threshold it had to clear, and asserts that at
    least one intent can clear its own threshold on at least one real sentence. A corpus
    that cannot do that has an embedding layer in name only.
    """
    import json as _json
    from pathlib import Path
    from intergen.intents import register_all_intents
    from intergen.semantic import SemanticMatcher

    try:
        probe = _real_embedder(["probe"])
    except (urllib.error.URLError, OSError, KeyError) as exc:
        pytest.fail(
            f"The embedding server is not answering at {EMBED_URL} ({exc}). Refusing to "
            "report a corpus measurement taken with the embedding layer absent.")
    if not probe or not probe[0]:
        pytest.fail(f"The embedding server at {EMBED_URL} returned an empty vector.")

    fixtures = Path(__file__).resolve().parent / "fixtures" / "field_sentences.json"
    sentences = [s["sentence"] for s in
                 _json.loads(fixtures.read_text(encoding="utf-8"))["sentences"]]

    m = SemanticMatcher(embedder=_real_embedder)
    register_all_intents(m)
    if m.get_intent_count() <= 0:
        pytest.fail("The shipped intent corpus registered zero intents.")

    import numpy as np
    query_vectors = _real_embedder(sentences)
    intents = list(m._embedding_intents.values())

    rows = []
    for intent in intents:
        best = 0.0
        best_sentence = ""
        for sentence, qv in zip(sentences, query_vectors):
            sims = m._cosine_similarity(np.asarray(qv, dtype=float), intent.embeddings)
            s = float(np.max(sims))
            if s > best:
                best, best_sentence = s, sentence
        rows.append((intent.intent_id, best, intent.threshold, best_sentence))

    clearing = [r for r in rows if r[1] >= r[2]]
    highest = max(rows, key=lambda r: r[1])
    record_property("intents_swept", len(rows))
    record_property("intents_clearing_threshold", len(clearing))
    record_property("highest_similarity", round(highest[1], 4))

    report = ["", f"CORPUS SWEEP — {len(rows)} shipped intents against "
                  f"{len(sentences)} real field sentences", ""]
    for iid, best, threshold, sentence in sorted(rows, key=lambda r: -r[1]):
        report.append(f"  {'CLEARS' if best >= threshold else '      '} "
                      f"{best:.4f} vs threshold {threshold:.2f}  {iid}"
                      f"   best on: {sentence!r}")
    report.append("")
    report.append(
        f"Highest similarity reached by any shipped intent on any real sentence: "
        f"{highest[1]:.4f} ({highest[0]}), against its threshold {highest[2]:.2f}.")
    report.append(
        "Lowering thresholds is not the remedy on its own: the ordering of these rows "
        "decides which tool a lower floor would admit first, and it is not the "
        "sentence's own tool in every case. The corpus is the fix surface.")

    assert clearing, "\n".join(report)
