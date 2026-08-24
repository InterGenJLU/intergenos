"""Layer-2 must return ONE candidate — its name, its tool, and its own score.

WHY THIS FILE EXISTS. `_match_embeddings` kept one running best score and one
running best intent but updated them under different conditions: the score
advanced for any candidate that beat it, the name and tool only for a candidate
that also cleared its OWN threshold. Per-intent thresholds differ across the
shipped corpus (0.85, 0.88, 0.90), so a higher-scoring candidate that was NOT
eligible could raise the reported score while the reported name and tool still
belonged to someone else.

Two things followed, and both are pinned below:

  * the returned tuple named a candidate whose own similarity it did not carry,
    and the router's own admission gate (score >= 0.85) could therefore be
    satisfied by a number belonging to a different candidate — admitting, and
    dispatching, an intent whose real similarity was under the gate;
  * an ELIGIBLE candidate could be discarded entirely, returning no intent at
    all, purely because a higher-scoring ineligible one was registered first —
    which made Layer 2 depend on registration order.

Every similarity in this file is a number chosen here: the embedder is a lookup
table of fixed unit vectors, so no model, no embedding server and no network are
involved and the arithmetic is exact.
"""

import math
import unittest

import numpy as np

from intergen.semantic import SemanticMatcher

QUERY = "QUERY"


def _unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def _at_cosine(target, axis):
    """A unit vector whose cosine with the query vector is exactly `target`."""
    s = math.sqrt(max(0.0, 1.0 - target * target))
    v = [target, 0.0, 0.0]
    v[axis] = s
    return _unit(v)


def _matcher(intents):
    """intents: [(intent_id, cosine_with_query, threshold, tool_name)]."""
    vectors = {QUERY: _unit([1.0, 0.0, 0.0])}
    for i, (intent_id, cosine, _threshold, _tool) in enumerate(intents):
        vectors["ex_" + intent_id] = _at_cosine(cosine, 1 + (i % 2))
    def embedder(texts):
        return [[float(x) for x in vectors[t]] for t in texts]
    m = SemanticMatcher(embedder=embedder)
    for intent_id, _cosine, threshold, tool in intents:
        m.register_intent(intent_id, ["ex_" + intent_id],
                          threshold=threshold, tool_name=tool)
    return m


class TestSelectedCandidateIsSelfConsistent(unittest.TestCase):
    """The name, the tool and the score must describe the same candidate."""

    def test_control_uniform_thresholds_returns_the_top_candidate(self):
        """Control: with equal thresholds the highest scorer wins outright.

        If this ever fails, the rest of this file is measuring the fixture and
        not the matcher.
        """
        r = _matcher([("alpha", 0.80, 0.70, "tool_alpha"),
                      ("beta", 0.95, 0.70, "tool_beta")])._match_embeddings(QUERY)
        self.assertEqual(r.intent_id, "beta")
        self.assertEqual(r.tool_name, "tool_beta")
        self.assertAlmostEqual(r.score, 0.95, places=4)

    def test_score_belongs_to_the_returned_intent(self):
        """A higher-scoring INELIGIBLE candidate must not lend its score."""
        r = _matcher([("alpha", 0.80, 0.75, "tool_alpha"),   # eligible
                      ("beta", 0.95, 0.99, "tool_beta")      # higher, NOT eligible
                      ])._match_embeddings(QUERY)
        self.assertEqual(r.intent_id, "alpha")
        self.assertEqual(r.tool_name, "tool_alpha")
        self.assertAlmostEqual(
            r.score, 0.80, places=4,
            msg="the returned score is not the returned intent's own similarity")

    def test_the_admission_gate_cannot_be_satisfied_by_another_candidate(self):
        """The router admits on score >= 0.85; that number must be the winner's.

        alpha is eligible at 0.80 by its own low threshold but is UNDER the
        router's 0.85 gate. beta scores 0.95 and is ineligible. Nothing here may
        hand alpha a 0.95 and get it admitted.
        """
        r = _matcher([("alpha", 0.80, 0.75, "tool_alpha"),
                      ("beta", 0.95, 0.99, "tool_beta")])._match_embeddings(QUERY)
        admitted = r.intent_id is not None and r.score >= 0.85
        self.assertFalse(
            admitted,
            "the router's 0.85 gate admitted an intent whose own similarity is "
            "0.80, on a score belonging to a different candidate")

    def test_an_eligible_candidate_is_not_discarded(self):
        """A higher-scoring ineligible candidate must not erase an eligible one."""
        r = _matcher([("beta", 0.95, 0.99, "tool_beta"),     # registered FIRST
                      ("alpha", 0.80, 0.75, "tool_alpha")    # eligible, second
                      ])._match_embeddings(QUERY)
        self.assertEqual(
            r.intent_id, "alpha",
            "an eligible candidate was discarded because an ineligible one "
            "scored higher and registered first")
        self.assertAlmostEqual(r.score, 0.80, places=4)

    def test_registration_order_does_not_change_the_result(self):
        """Layer 2 is an argmax over eligible candidates, so it is order-free."""
        forward = _matcher([("alpha", 0.80, 0.75, "tool_alpha"),
                            ("beta", 0.95, 0.99, "tool_beta")])._match_embeddings(QUERY)
        reverse = _matcher([("beta", 0.95, 0.99, "tool_beta"),
                            ("alpha", 0.80, 0.75, "tool_alpha")])._match_embeddings(QUERY)
        self.assertEqual(forward.intent_id, reverse.intent_id)
        self.assertEqual(forward.tool_name, reverse.tool_name)
        self.assertAlmostEqual(forward.score, reverse.score, places=6)

    def test_highest_eligible_candidate_wins_among_several(self):
        """With more than one eligible candidate the strongest is selected."""
        r = _matcher([("alpha", 0.86, 0.85, "tool_alpha"),
                      ("gamma", 0.91, 0.90, "tool_gamma"),
                      ("beta", 0.95, 0.99, "tool_beta")])._match_embeddings(QUERY)
        self.assertEqual(r.intent_id, "gamma")
        self.assertAlmostEqual(r.score, 0.91, places=4)

    def test_nothing_eligible_returns_no_intent_and_no_borrowed_score(self):
        r = _matcher([("alpha", 0.60, 0.75, "tool_alpha"),
                      ("beta", 0.95, 0.99, "tool_beta")])._match_embeddings(QUERY)
        self.assertIsNone(r.intent_id)
        self.assertIsNone(r.tool_name)
        self.assertAlmostEqual(r.score, 0.0, places=6)


class TestObservabilityIsPreserved(unittest.TestCase):
    """Making the score attributable must not lose the near-miss signal."""

    def test_top_score_still_reports_the_best_similarity_seen(self):
        r = _matcher([("alpha", 0.80, 0.75, "tool_alpha"),
                      ("beta", 0.95, 0.99, "tool_beta")])._match_embeddings(QUERY)
        self.assertAlmostEqual(
            r.top_score, 0.95, places=4,
            msg="the highest similarity observed is no longer reported anywhere, "
                "so a near-miss under an intent's own threshold is now invisible")

    def test_top_score_is_reported_even_when_nothing_is_eligible(self):
        r = _matcher([("alpha", 0.60, 0.75, "tool_alpha"),
                      ("beta", 0.95, 0.99, "tool_beta")])._match_embeddings(QUERY)
        self.assertIsNone(r.intent_id)
        self.assertAlmostEqual(r.top_score, 0.95, places=4)


class TestShippedCorpusShapeIsAffected(unittest.TestCase):
    """The precondition is met by the corpus this release actually ships."""

    def test_the_shipped_corpus_uses_more_than_one_threshold(self):
        from intergen import intents as intents_module
        import re
        source = open(intents_module.__file__).read()
        thresholds = {float(m) for m in re.findall(r"threshold=([0-9.]+)", source)}
        self.assertGreater(
            len(thresholds), 1,
            "this test's premise has changed: the corpus now uses one threshold")


if __name__ == "__main__":
    unittest.main()
