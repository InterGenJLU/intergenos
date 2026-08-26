# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The router admits a semantic match on the intent's OWN bar, not on a second 0.85 floor.

WHAT WAS WRONG. ``SemanticMatcher._match_embeddings`` already applies each intent's own
threshold: it is an argmax over the candidates that clear their OWN bar, and it returns
no intent at all when none of them does. The router then applied a SECOND, flat 0.85
floor to the number that came back, at two seams:

    route()               if p2_match.intent_id is not None and p2_match.score >= 0.85
    _try_semantic_match() if match.intent_id is None or match.score < 0.85

Every intent whose own threshold is BELOW 0.85 was therefore unreachable through the
router by exactly the amount of that difference. The shipped corpus has one such intent
and it is the one real people need most: ``web_search`` sits at 0.68, a number that was
measured rather than tuned — the corpus separates a real look-it-up request (0.7266 and
0.8241 on the two sentences below) from the highest-scoring non-request (0.5746). The
0.85 floor threw that separation away and refused both requests.

THE TWO SENTENCES. Both are verbatim from the sealed field trace of the first outside
user's two sessions (``tests/installed/fixtures/field_sentences.json``, #15 and #17).
Neither carries a look-it-up VERB, so the keyword layer cannot take them; the embedding
layer is the only layer that can, it recognised both, and the router discarded the
recognition:

    #15  "What is the value of a chippendale dining table with ball and claw feet?"
    #17  "Can I find a free egg apron sewing pattern?"

THE RISK THIS FILE EXISTS TO BOUND. Removing an admission check can only be safe if the
bar underneath it is real. The negative half of the fixture — the sentences that must
NOT reach a tool — is asserted here as a HELD set, at the same time and by the same
path, because "we served two more" is not a result until "we falsely served none" is
measured beside it.

TWO CLASSES, ON PURPOSE.
  * :class:`RouterAdmissionIsTheMatchersBar` runs everywhere, with Layer 2 replaced by
    one fixed verdict, so the CODE CONTRACT is pinned in the ordinary suite on a box
    with no embedding server. The scores it feeds are the two real measurements above,
    not invented numbers.
  * :class:`FieldSentencesThroughTheRealRouter` runs the whole nineteen against the real
    corpus and the REAL embedding server, and is the proof that the contract above
    changes what a real person gets. It is opt-in because it needs that server; when it
    is asked for and the server is absent it FAILS, it does not skip — "I could not
    check" must never read as "I checked".

NOTHING IS EXECUTED IN EITHER CLASS. ``_execute_tool_for_intent`` is replaced by a
recorder throughout: the subject is the ADMISSION decision, and a test about routing
must not put a web search on the wire.
"""

from __future__ import annotations

import hashlib
import json
import os
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from intergen.interfaces.semantic import MatchResult
from intergen.interfaces.types import Provenance, ToolCall, ToolResult
from intergen.intents import register_all_intents
from intergen.llm import LLMRouter
from intergen.router import ConversationRouter
from intergen.semantic import SemanticMatcher
from intergen.tool_registry import ToolRegistry

# The fixture is the sealed field trace; this file never retypes a sentence from it.
_FIXTURE = (Path(__file__).resolve().parents[2]
            / "tests" / "installed" / "fixtures" / "field_sentences.json")

# The two sentences the 0.85 floor refused, and the similarity each one really reached
# against the shipped web_search corpus on this project's embedding model. Measured, not
# chosen: see the survey capture sealed with this cut.
_MEASURED = {15: 0.7266, 17: 0.8241}
_WEB_SEARCH_THRESHOLD = 0.68

_LIVE_ENV = "INTERGEN_FIELD_ROUTER_GATE"
_EMBED_URL = os.environ.get("INTERGEN_FIELD_ROUTER_EMBED_URL",
                            "http://127.0.0.1:8081/v1/embeddings")

_REGISTRY = ToolRegistry()
_REGISTRY.discover_tools()


def _load_fixture() -> list[dict]:
    """Read the field sentences, and refuse to proceed if the file has drifted.

    The fixture carries a ``corpus_sha256`` over its own sentence list. Measured
    2026-08-26: the value is the sha256 of the nineteen sentences joined by newlines
    with a trailing newline, it is CORRECT for the file as it stands — and NOTHING in
    the tree read it. A declared integrity value that no code checks is not integrity;
    it is a comment shaped like a digest. It is checked here because this cut's whole
    claim rests on these being the sealed field sentences rather than sentences someone
    later found convenient, and because a sentence edited to make a routing gate pass is
    exactly the failure the digest was put there to catch.

    The digest covers the SENTENCES only, deliberately: the verdicts, rationales and the
    router-level records beside them are meant to be revised as they are measured.
    """
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    sentences = payload["sentences"]
    if len(sentences) != payload["sentence_count"]:
        raise AssertionError(
            f"{_FIXTURE} declares {payload['sentence_count']} sentences but carries "
            f"{len(sentences)}. A fixture set that cannot count itself is not one.")
    body = "\n".join(e["sentence"] for e in sentences) + "\n"
    measured = hashlib.sha256(body.encode("utf-8")).hexdigest()
    declared = payload["corpus_sha256"]
    if measured != declared:
        raise AssertionError(
            f"{_FIXTURE} declares corpus_sha256 {declared} but its nineteen sentences "
            f"hash to {measured}. A field sentence has been changed. Re-derive the "
            f"digest from the sealed trace and say what moved — never re-stamp it to "
            f"match an edit.")
    return sentences


def _sentence(index: int) -> str:
    for entry in _load_fixture():
        if entry["index"] == index:
            return entry["sentence"]
    raise AssertionError(f"field sentence #{index} is not in {_FIXTURE}")


class _Recorder:
    """Stands in for ConversationRouter._execute_tool_for_intent.

    Records the tool the router chose to dispatch and returns a synthetic success.
    Nothing runs. The recorder is also the ASSERTION SURFACE: a dispatch that was
    admitted but never reached a tool would leave this empty, so "admitted" is read
    from the attempt, not from the reply text.
    """

    def __init__(self) -> None:
        self.tools: list[str] = []

    def __call__(self, tool_name: str, user_input: str):
        self.tools.append(tool_name)
        call = ToolCall(name=tool_name, arguments={}, call_id="test-admission",
                        source_of_request=Provenance.USER_DIRECT)
        result = ToolResult(call_id="test-admission", name=tool_name,
                            content="TEST STUB OUTPUT — nothing was executed",
                            success=True)
        return call, result


class _FixedLayerTwoMatcher(SemanticMatcher):
    """The shipped matcher with Layer 2 replaced by one fixed verdict.

    Layer 0 normalisation, the keyword layer and the real intent corpus are untouched,
    so a sentence the keyword layer owns is still taken by the keyword layer. Only the
    embedding layer's ANSWER is held constant, which is what lets this class measure the
    router's treatment of that answer without an embedding server.
    """

    def __init__(self, verdict: MatchResult) -> None:
        super().__init__(embedder=None)
        self._fixed_verdict = verdict

    def _match_embeddings(self, query: str) -> MatchResult:   # noqa: D102
        return self._fixed_verdict


def _router(matcher: SemanticMatcher) -> ConversationRouter:
    return ConversationRouter(tool_registry=_REGISTRY, semantic_matcher=matcher,
                              llm=LLMRouter(config=None), lock_dispatch=True)


def _selected(intent_id: str, tool_name: str, score: float) -> MatchResult:
    """A verdict shaped exactly as _match_embeddings returns one for a SELECTED
    candidate: the intent cleared its OWN threshold and the score describes it."""
    return MatchResult(intent_id=intent_id, score=score, layer="embedding",
                       tool_name=tool_name, runner_up_score=0.0, top_score=score)


def _nothing_eligible(top_score: float) -> MatchResult:
    """A verdict shaped exactly as _match_embeddings returns one when NO candidate
    cleared its own threshold: no intent, no borrowed score, near-miss still visible."""
    return MatchResult(intent_id=None, score=0.0, layer="embedding",
                       tool_name=None, runner_up_score=0.0, top_score=top_score)


class RouterAdmissionIsTheMatchersBar(unittest.TestCase):
    """The router's admission is the matcher's verdict — no second floor of its own."""

    def _route(self, matcher: SemanticMatcher, sentence: str):
        recorder = _Recorder()
        r = _router(matcher)
        with mock.patch.object(ConversationRouter, "_execute_tool_for_intent",
                               lambda self, t, u: recorder(t, u)):
            result = r.route(sentence, decide_only=True)
        return result, recorder

    def test_a_sub_085_selection_is_admitted_at_the_route_seam(self):
        """route() must call the semantic path for any intent the matcher SELECTED.

        This is the seam that decides whether _try_semantic_match runs at all. While it
        carried `p2_match.score >= 0.85`, deleting the floor inside _try_semantic_match
        changed nothing on the main route path — the method was never reached.
        """
        for index, score in _MEASURED.items():
            with self.subTest(field_sentence=index, score=score):
                self.assertGreaterEqual(
                    score, _WEB_SEARCH_THRESHOLD,
                    "the fixture score must clear web_search's own bar or this "
                    "subtest is asserting the wrong thing")
                self.assertLess(score, 0.85,
                                "and it must sit under the old flat floor")
                matcher = _FixedLayerTwoMatcher(
                    _selected("web_search", "web_search", score))
                register_all_intents(matcher)
                result, recorder = self._route(matcher, _sentence(index))
                self.assertEqual(
                    result.source, "semantic",
                    f"field sentence #{index} scored {score} against web_search's own "
                    f"threshold of {_WEB_SEARCH_THRESHOLD} and the matcher SELECTED it; "
                    f"the router answered from '{result.source}' instead of dispatching")
                self.assertEqual(
                    recorder.tools, ["web_search"],
                    f"field sentence #{index} was admitted but no tool was attempted")

    def test_a_sub_085_selection_is_admitted_inside_try_semantic_match(self):
        """The re-check inside _try_semantic_match is gone too.

        Asserted separately from the route seam because the two are independent: the
        method is ALSO called unguarded from _route_single (the decomposed sub-query
        path), where this re-check was the only bar.
        """
        for index, score in _MEASURED.items():
            with self.subTest(field_sentence=index, score=score):
                matcher = _FixedLayerTwoMatcher(
                    _selected("web_search", "web_search", score))
                register_all_intents(matcher)
                recorder = _Recorder()
                r = _router(matcher)
                with mock.patch.object(ConversationRouter, "_execute_tool_for_intent",
                                       lambda self, t, u: recorder(t, u)):
                    result = r._try_semantic_match(_sentence(index))
                self.assertTrue(
                    result.handled,
                    f"_try_semantic_match refused a SELECTED intent scoring {score}")
                self.assertEqual(result.source, "semantic")
                self.assertEqual(recorder.tools, ["web_search"])

    def test_the_score_the_router_reports_is_the_selected_candidates_own(self):
        """Admission does not distort the confidence the turn carries."""
        score = _MEASURED[17]
        matcher = _FixedLayerTwoMatcher(_selected("web_search", "web_search", score))
        register_all_intents(matcher)
        recorder = _Recorder()
        r = _router(matcher)
        with mock.patch.object(ConversationRouter, "_execute_tool_for_intent",
                               lambda self, t, u: recorder(t, u)):
            result = r._try_semantic_match(_sentence(17))
        # Assert admission FIRST. An unhandled RouteResult carries confidence 1.0 by
        # default, so without this line the failure at base read "1.0 != 0.8241" — a
        # true failure reported through a field that was never set, which names the
        # wrong cause to whoever reads it next.
        self.assertTrue(result.handled,
                        "the turn was refused, so there is no reported confidence to "
                        "compare — fix the admission before reading this number")
        self.assertAlmostEqual(result.confidence, score, places=6)

    def test_no_selection_is_still_refused(self):
        """THE CONTROL. Removing the floor must not turn the seam into "admit anything".

        When the matcher reports that NOTHING cleared its own threshold — the shape it
        returns for every negative sentence in the fixture — the router must dispatch no
        tool, even though the near-miss score it can see is high.
        """
        for near_miss in (0.0, 0.5746, 0.8499, 0.99):
            with self.subTest(top_score=near_miss):
                matcher = _FixedLayerTwoMatcher(_nothing_eligible(near_miss))
                register_all_intents(matcher)
                recorder = _Recorder()
                r = _router(matcher)
                with mock.patch.object(ConversationRouter, "_execute_tool_for_intent",
                                       lambda self, t, u: recorder(t, u)):
                    result = r._try_semantic_match(
                        "what year did babe ruth start playing baseball?")
                self.assertFalse(
                    result.handled,
                    f"the router dispatched on a verdict that selected NO intent "
                    f"(near-miss {near_miss}) — the bar underneath is gone, not moved")
                self.assertEqual(recorder.tools, [],
                                 "a tool was attempted with no intent selected")


class TheBlastRadiusOfRemovingTheFloorIsBounded(unittest.TestCase):
    """Which intents can this change newly admit, and can that answer drift silently?

    Removing a flat 0.85 floor changes admission for exactly one population: intents
    whose OWN threshold is below 0.85, in the band between their threshold and 0.85.
    An intent sitting AT 0.85 is unaffected (the old gate admitted a score of exactly
    0.85 and so does the matcher's own bar), and an intent above 0.85 was already the
    binding constraint on itself.

    Measured on the shipped corpus 2026-08-26, exactly one intent is in that band —
    web_search at 0.68 — so the whole behavioural change this cut can produce is
    web_search dispatches scoring between 0.68 and 0.85. That is a small enough
    surface to have measured directly, and the field fixture measures it.

    This test exists so the sentence above cannot quietly stop being true. Lowering
    some other intent's threshold below 0.85 is a legitimate thing to do, but it
    widens what the router now admits, and whoever does it should be told here rather
    than finding out from a wrong dispatch.
    """

    #: Every intent whose shipped threshold is under 0.85, with that threshold.
    #: Grown ONLY alongside a measurement of what the widened band admits.
    EXPECTED_BELOW_085 = {"web_search": 0.68}

    @staticmethod
    def _shipped_thresholds() -> dict[str, float]:
        """Every shipped embedding intent's own threshold, read from the corpus.

        A CONSTANT-SHAPE EMBEDDER IS USED ON PURPOSE, and the first draft of this
        helper did not have one. With ``embedder=None`` every intent registers as
        PENDING instead of live, ``_embedding_intents`` stays empty, and the
        inventory below reads ``{}`` — an empty set that would have compared clean
        against any expectation phrased as "nothing unexpected is below 0.85". The
        thresholds are declared in the corpus and do not depend on the vectors, so a
        fixed vector is enough to make registration complete and the reading real.
        """
        matcher = SemanticMatcher(embedder=lambda texts: [[1.0, 0.0] for _ in texts])
        register_all_intents(matcher)
        if matcher._pending_embedding_intents:
            raise AssertionError(
                "intents registered as PENDING rather than live: "
                f"{sorted(matcher._pending_embedding_intents)}. The inventory below "
                "would under-report, so it is refused rather than reported.")
        return {i.intent_id: i.threshold
                for i in matcher._embedding_intents.values()}

    def test_only_the_measured_intents_sit_below_the_old_floor(self):
        thresholds = self._shipped_thresholds()
        self.assertTrue(thresholds, "the shipped intent corpus registered nothing")
        below = {name: thr for name, thr in thresholds.items() if thr < 0.85}
        self.assertEqual(
            below, self.EXPECTED_BELOW_085,
            "the set of intents sitting below the removed 0.85 floor has changed.\n"
            f"  shipped thresholds : {dict(sorted(thresholds.items()))}\n"
            f"  below 0.85 now     : {dict(sorted(below.items()))}\n"
            f"  measured and proven: {self.EXPECTED_BELOW_085}\n"
            "Every intent in this set is admissible through the router at scores the "
            "old floor refused. Measure what the new band admits — positives AND "
            "non-requests — and record it beside this set before widening it.")

    def test_an_intent_at_the_old_floor_is_not_affected(self):
        """The boundary case, stated so it is not re-derived by hand later."""
        at_floor = [name for name, thr in self._shipped_thresholds().items()
                    if thr == 0.85]
        self.assertTrue(
            at_floor,
            "no intent sits exactly at 0.85, so this boundary case is untested "
            "against the real corpus — check whether the corpus moved")
        for name in at_floor:
            with self.subTest(intent=name):
                matcher2 = _FixedLayerTwoMatcher(_selected(name, "run_command", 0.85))
                register_all_intents(matcher2)
                recorder = _Recorder()
                r = _router(matcher2)
                with mock.patch.object(ConversationRouter, "_execute_tool_for_intent",
                                       lambda self, t, u: recorder(t, u)):
                    result = r._try_semantic_match("a sentence that reaches layer two")
                self.assertTrue(
                    result.handled,
                    f"{name} scores exactly at its own threshold of 0.85 and was "
                    f"admitted before this change; it must still be admitted")


def _real_embedder(texts: list[str]):
    """The same request shape the shipped llama manager uses. No mock, no stub."""
    if not texts:
        return []
    body = json.dumps({"input": list(texts), "model": "embedding"}).encode()
    req = urllib.request.Request(_EMBED_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180.0) as resp:
        data = json.loads(resp.read())
    rows = data.get("data")
    if not isinstance(rows, list) or len(rows) != len(texts):
        raise RuntimeError(
            f"embedding server returned "
            f"{len(rows) if isinstance(rows, list) else -1} rows for {len(texts)} inputs")
    return [r["embedding"] for r in sorted(rows, key=lambda r: r.get("index", 0))]


@unittest.skipUnless(
    os.environ.get(_LIVE_ENV) == "1",
    f"live field-sentence router gate is opt-in: set {_LIVE_ENV}=1 with the embedding "
    f"server reachable at {_EMBED_URL}. NOTHING about the real corpus is verified "
    f"while this is skipped.")
class FieldSentencesThroughTheRealRouter(unittest.TestCase):
    """All nineteen field sentences through the real router, corpus and embedder.

    A run that cannot reach the embedding server FAILS here. This class's whole subject
    is what the deterministic layers do on real language against the real corpus; a run
    with the embedding layer silently absent would measure the keyword layer alone and
    report a number that looks like an answer.
    """

    @classmethod
    def setUpClass(cls):
        try:
            probe = _real_embedder(["probe"])
        except Exception as exc:                       # noqa: BLE001 — reported, not hidden
            raise AssertionError(
                f"{_LIVE_ENV}=1 was set but the embedding server at {_EMBED_URL} is not "
                f"answering ({exc!r}). REFUSING to report a routing verdict measured "
                f"with the embedding layer absent.") from exc
        if not probe or not probe[0]:
            raise AssertionError(
                f"the embedding server at {_EMBED_URL} returned an empty vector.")
        cls.sentences = _load_fixture()

    def _drive(self, sentence: str) -> tuple[str, list[str]]:
        matcher = SemanticMatcher(embedder=_real_embedder)
        register_all_intents(matcher)
        if matcher.get_intent_count() <= 0:
            raise AssertionError("the shipped intent corpus registered zero intents.")
        recorder = _Recorder()
        r = _router(matcher)
        with mock.patch.object(ConversationRouter, "_execute_tool_for_intent",
                               lambda self, t, u: recorder(t, u)):
            result = r.route(sentence, decide_only=True)
        return result.source, recorder.tools

    def test_the_two_refused_field_sentences_are_now_served(self):
        """#15 and #17 reach the web_search dispatch instead of falling to the model."""
        for index in sorted(_MEASURED):
            with self.subTest(field_sentence=index):
                source, tools = self._drive(_sentence(index))
                self.assertEqual(
                    source, "semantic",
                    f"field sentence #{index} still falls through to '{source}'. It "
                    f"scores {_MEASURED[index]} against web_search's own threshold of "
                    f"{_WEB_SEARCH_THRESHOLD}; the matcher selects it and the router "
                    f"must not refuse it.")
                self.assertEqual(tools, ["web_search"],
                                 f"field sentence #{index} was routed but no web_search "
                                 f"dispatch was attempted")

    def test_the_negative_set_is_held(self):
        """THE RISK. No sentence owed 'not a tool turn' may reach any tool.

        This is asserted over the fixture's own negative half rather than a list written
        here, so widening the fixture widens the guard automatically.
        """
        negatives = [e for e in self.sentences
                     if e["expected_verdict"] == "not_a_tool_turn"]
        self.assertEqual(
            [e["index"] for e in negatives], [3, 5, 6, 10, 16, 18],
            "the fixture's negative set changed; the held set below must be re-measured "
            "before this gate can speak for it")
        admitted = []
        for entry in negatives:
            source, tools = self._drive(entry["sentence"])
            if tools:
                admitted.append((entry["index"], entry["sentence"], source, tools))
        self.assertEqual(
            admitted, [],
            "a sentence owed NO tool was dispatched to one:\n" + "\n".join(
                f"  #{i} via {s} -> {t}  :: {sent}" for i, sent, s, t in admitted))

    def test_every_sentence_owed_a_tool_gets_one_or_is_named(self):
        """The full nineteen, reported as a table, with the two carve-outs named.

        Sentences 1 and 2 are owed a SERVED verdict and NOT a named tool — the router
        serves #1 from a code-owned direct answer and #2 from the explain gate, so no
        tool is dispatched for either and none should be. The fixture records that;
        this test reads it from the fixture rather than restating it.
        """
        rows, unserved = [], []
        for entry in self.sentences:
            source, tools = self._drive(entry["sentence"])
            owed_served = entry["expected_verdict"] == "recognised"
            served = bool(tools) or source in entry.get("served_by", [])
            rows.append((entry["index"], entry["expected_verdict"],
                         entry.get("expected_tool"), source, tools, served))
            if owed_served and not served:
                unserved.append((entry["index"], entry["sentence"], source))
        report = ["", "ROUTER VERDICT OVER THE NINETEEN FIELD SENTENCES", ""]
        for index, owed, tool, source, tools, served in rows:
            report.append(f"  {index:2d} owed={owed}/{tool} via={source} "
                          f"dispatched={tools} served={served}")
        report.append("")
        self.assertEqual(unserved, [], "\n".join(report) + "\nUNSERVED: " + repr(unserved))


if __name__ == "__main__":
    unittest.main()
