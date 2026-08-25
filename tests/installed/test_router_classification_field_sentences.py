"""GATE 1 — router classification accuracy over the real field sentences.

THE NUMBER THIS GATE PRODUCES IS THE CEILING ON EVERYTHING ELSE. When the router's
pre-model gate misclassifies a turn there is no recovery path: the browser interface
attaches tool schemas only when the router has already decided the turn is a tool
turn, so a turn the router does not recognise reaches the model with no tools and
comes back as a refusal or an invented answer. Classification accuracy is therefore
not one quality measure among several — it bounds what the assistant can do at all.

WHAT IS MEASURED. Each of the nineteen sentences is put through the SHIPPED
deterministic layers, in the order the shipped matcher uses them: Layer 0
normalisation first, then the keyword layer, then the embedding layer against the
SHIPPED intent corpus using the REAL embedding server this machine runs. For each sentence the gate records the verdict
(recognised with a tool, or not a tool turn) and compares it with the verdict that
sentence is owed.

WHY THE EMBEDDING SERVER IS NOT MOCKED. The defect this gate exists to catch is a
property of the real corpus against the real model: measured on the released system,
the highest similarity any shipped intent reached on any of these sentences was
0.7104 against a lowest threshold of 0.85, so the embedding layer contributed
nothing. A mocked embedder would have reported a healthy matcher.

EXPECTED TO FAIL ON R001.1 AS SHIPPED. That is the point of a red-first fixture. If
this gate ever passes on an unfixed R001.1 the fixture is wrong and must be reported,
not softened.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "field_sentences.json"
EMBED_URL = "http://127.0.0.1:8081/v1/embeddings"


def _load_fixtures() -> list[dict]:
    payload = json.loads(FIXTURES.read_text(encoding="utf-8"))
    sentences = payload["sentences"]
    if len(sentences) != payload["sentence_count"]:
        pytest.fail(
            f"{FIXTURES} declares {payload['sentence_count']} sentences but carries "
            f"{len(sentences)}. A fixture set that cannot count itself is not a fixture set."
        )
    return sentences


def _real_embedder(texts: list[str]):
    """The same request shape the shipped llama manager uses. No mock, no stub."""
    if not texts:
        return []
    body = json.dumps({"input": list(texts), "model": "embedding"}).encode()
    req = urllib.request.Request(
        EMBED_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120.0) as resp:
        data = json.loads(resp.read())
    rows = data.get("data")
    if not isinstance(rows, list) or len(rows) != len(texts):
        raise RuntimeError(
            f"embedding server returned {len(rows) if isinstance(rows, list) else -1} "
            f"rows for {len(texts)} inputs")
    return [r["embedding"] for r in sorted(rows, key=lambda r: r.get("index", 0))]


@pytest.fixture(scope="module")
def matcher(installed_intergen_dir):
    """The SHIPPED matcher, carrying the SHIPPED intent corpus and the REAL embedder.

    A matcher that cannot reach the embedding server is a FAILURE here, never a skip.
    This gate's whole subject is what the deterministic layers do on real language; a
    run with the embedding layer silently absent would measure the keyword layer alone
    and report a number that looks like an answer.
    """
    import sys
    sys.path.insert(0, str(installed_intergen_dir.parent))
    from intergen.intents import register_all_intents
    from intergen.semantic import SemanticMatcher

    try:
        probe = _real_embedder(["probe"])
    except (urllib.error.URLError, OSError, RuntimeError) as exc:
        pytest.fail(
            "The embedding server this gate needs is not answering at "
            f"{EMBED_URL} ({exc}). REFUSING to report a classification number "
            "measured with the embedding layer absent — that number would describe "
            "the keyword layer alone while appearing to describe the router."
        )
    if not probe or not probe[0]:
        pytest.fail(f"The embedding server at {EMBED_URL} returned an empty vector.")

    m = SemanticMatcher(embedder=_real_embedder)
    register_all_intents(m)
    if m.get_intent_count() <= 0:
        pytest.fail("The shipped intent corpus registered zero intents.")
    return m


def _classify(m, sentence: str) -> tuple[str, str | None, str]:
    """Return (verdict, tool, layer) using the SHIPPED deterministic layers.

    LAYER 0 IS PART OF THE SHIPPED PATH AND WAS MISSING HERE. Corrected 2026-08-25.
    ``SemanticMatcher.match`` normalises the query BEFORE the keyword layer sees it —
    it strips bounded request-framing filler such as "can you" and "please" — and this
    gate called ``_match_keywords`` on the RAW sentence, so it was measuring something
    the product does not do. Measured on the released corpus: "can you update this
    system for me?" normalises to "update this system for me?", which the shipped
    manage_packages keyword takes; without normalisation the gate scored it as
    unrecognised and reported 7/19 where the product path gives 8/19.

    A gate that under-reports is as wrong as one that over-reports: it would have
    credited a later change with a sentence the product already served.
    """
    query = m._normalize_input(sentence)
    kw = m._match_keywords(query)
    if kw.intent_id:
        return "recognised", kw.tool_name, "keyword"
    emb = m._match_embeddings(query)
    if emb.intent_id:
        return "recognised", emb.tool_name, "embedding"
    return "not_a_tool_turn", None, "none"


def test_every_field_sentence_is_classified_as_it_is_owed(matcher, record_property):
    """Every one of the nineteen sentences must get the verdict it is owed."""
    sentences = _load_fixtures()
    rows, wrong = [], []
    for entry in sentences:
        verdict, tool, layer = _classify(matcher, entry["sentence"])
        ok = verdict == entry["expected_verdict"] and (
            verdict != "recognised" or tool == entry["expected_tool"])
        rows.append((entry["index"], ok, verdict, tool, layer, entry))
        if not ok:
            wrong.append((entry, verdict, tool, layer))

    correct = sum(1 for r in rows if r[1])
    record_property("field_sentences_total", len(sentences))
    record_property("field_sentences_correct", correct)
    record_property("classification_accuracy", round(correct / len(sentences), 4))

    report = ["", f"CLASSIFICATION ACCURACY: {correct}/{len(sentences)} "
                  f"({correct / len(sentences):.1%})", ""]
    for index, ok, verdict, tool, layer, entry in rows:
        report.append(
            f"  {index:2d} {'OK  ' if ok else 'WRONG'} "
            f"owed={entry['expected_verdict']}/{entry['expected_tool']} "
            f"got={verdict}/{tool} via={layer}  :: {entry['sentence']}")
    report.append("")
    if wrong:
        report.append("Each WRONG line above is a turn a real first-time user typed and "
                      "did not get served. The verdicts are the contract, not a "
                      "description of current behaviour.")
    assert not wrong, "\n".join(report)


def test_the_embedding_layer_recognises_at_least_one_field_sentence(matcher):
    """The embedding layer must contribute SOMETHING on real language.

    A separate gate from accuracy, deliberately. Accuracy can be argued about one
    sentence at a time; this one asks a question with no room to argue — across
    nineteen sentences of real first-time-user language, does the semantic layer
    recognise even one? On R001.1 as shipped the answer is no, and that single fact
    explains most of the accuracy gap above.
    """
    sentences = _load_fixtures()
    recognised = []
    for entry in sentences:
        # Layer 0 first, for the reason recorded in _classify above.
        query = matcher._normalize_input(entry["sentence"])
        if matcher._match_keywords(query).intent_id:
            continue  # the keyword layer took it; this gate is about the layer behind it
        emb = matcher._match_embeddings(query)
        if emb.intent_id:
            recognised.append((entry["index"], entry["sentence"], emb.intent_id, emb.score))
    assert recognised, (
        "The embedding layer recognised NONE of the field sentences the keyword layer "
        "did not already take. On the released system the highest similarity any "
        "shipped intent reached on any of these sentences was 0.7104, against a lowest "
        "shipped threshold of 0.85 — so the layer cannot fire on real user language at "
        "all. Lowering thresholds is not the remedy: on four explicit web-search "
        "sentences the package-management intents outrank the web-search intent, so a "
        "lower floor admits the wrong tool sooner."
    )
