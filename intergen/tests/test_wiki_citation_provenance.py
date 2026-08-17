# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A wiki citation is a claim about where an answer came from.

Origin (2026-08-06, an end-to-end serving capture): a locally-served poem
request — the assistant answered on the machine, consulted no provider and no
setup documentation — came back as

    A lighthouse stands tall,
    Guiding ships through the dark.

    Source: [Frontier Provider Setup](file:///usr/share/doc/intergenos/wiki/...)

The page really exists in the shipped book and really verified against the signed
manifest, so nothing in the integrity chain was wrong. What was wrong is the
claim: the answer never used that page. Free-form retrieval put a passage in
front of the model as grounding, the model ignored it and wrote a poem from its
own weights, and the Source line was appended because a retrieval hit had
happened — not because the answer had a source.

The rule these tests hold: emission is conditional on the answer having actually
consumed the cited material, and an answer that consulted nothing carries NO
Source block rather than a plausible-looking one.

The planted control is that captured case: the answer text below is verbatim
from the serving log, and the request is stated as what it was, a request for a
short poem about a lighthouse.
"""

from __future__ import annotations

import unittest

from intergen.router import ConversationRouter
from intergen.semantic import SemanticMatcher
from intergen.llm import LLMRouter
from intergen.tool_registry import ToolRegistry
from intergen.wiki_retrieval import (
    MIN_ANSWER_SUPPORT,
    MIN_SHARED_WORDS,
    WikiHit,
    answer_support,
    answer_used_passage,
)

# ── the captured case: the answer is verbatim from the log ────────────────────
POEM_QUERY = "Write a short poem about a lighthouse."
POEM_ANSWER = "A lighthouse stands tall,\nGuiding ships through the dark."

# A real passage from the page that was cited — the frontier-provider setup
# documentation. Trimmed to a paragraph; the point is that the poem owes it
# nothing.
PROVIDER_PASSAGE = (
    "Frontier provider setup. InterGen can be configured to send a question to "
    "a remote provider when you ask it to. You supply an API key, choose which "
    "provider to use, and the assistant asks for your confirmation before any "
    "request leaves the machine. Nothing is sent without that confirmation, and "
    "the key is stored in the system keyring rather than in a configuration "
    "file."
)

# A grounded pair: a question about that same page, and an answer a model would
# produce FROM the passage.
PROVIDER_QUERY = "How do I set up a remote provider?"
GROUNDED_ANSWER = (
    "You supply an API key and choose which provider to use. The assistant asks "
    "for your confirmation before any request leaves the machine, and the key "
    "is stored in the system keyring rather than in a configuration file."
)

CITATION = ("Source: [Frontier Provider Setup]"
            "(file:///usr/share/doc/intergenos/wiki/assistant/"
            "frontier-provider-setup.html) · "
            "[online](https://wiki.intergenos.org/assistant/"
            "frontier-provider-setup.html)")


class AnswerSupportMeasure(unittest.TestCase):
    """The measure itself: how much of an answer a passage actually supplies."""

    def test_the_poem_owes_the_cited_passage_nothing(self):
        support = answer_support(POEM_ANSWER, PROVIDER_PASSAGE, POEM_QUERY)
        self.assertEqual(
            support, 0.0,
            "the captured poem shares no content word with the page it cited")

    def test_a_grounded_answer_is_measurably_supported(self):
        support = answer_support(GROUNDED_ANSWER, PROVIDER_PASSAGE,
                                 PROVIDER_QUERY)
        self.assertGreater(
            support, MIN_ANSWER_SUPPORT,
            "an answer written from the passage must measure as supported by it")

    def test_echoing_the_question_earns_nothing(self):
        # Every content word of this "answer" came from the user, not the page,
        # so the page supplied none of it even though the page contains those
        # same words.
        echo = "Remote provider setup."
        self.assertEqual(
            answer_support(echo, PROVIDER_PASSAGE,
                           "remote provider setup"), 0.0)

    def test_an_empty_answer_is_not_supported(self):
        self.assertEqual(answer_support("", PROVIDER_PASSAGE, POEM_QUERY), 0.0)


class AnswerUsedPassageGate(unittest.TestCase):
    """The verdict the citation hangs on."""

    def test_the_poem_case_is_refused(self):
        self.assertFalse(
            answer_used_passage(POEM_ANSWER, PROVIDER_PASSAGE, POEM_QUERY),
            "an answer that consulted nothing must not qualify for a citation")

    def test_the_grounded_case_is_allowed(self):
        self.assertTrue(
            answer_used_passage(GROUNDED_ANSWER, PROVIDER_PASSAGE,
                                PROVIDER_QUERY))

    def test_a_handful_of_shared_words_is_not_enough_on_its_own(self):
        # A very short answer can reach a high fraction on coincidence; the
        # shared-word floor is what stops that.
        tiny = "the keyring"
        self.assertLess(len({"keyring"}), MIN_SHARED_WORDS)
        self.assertFalse(answer_used_passage(tiny, PROVIDER_PASSAGE, ""))

    def test_the_verdict_does_not_depend_on_an_embedding_server(self):
        # Same inputs, called twice, with nothing injected: the gate is pure text
        # arithmetic, so it decides identically whether or not the embedder is up.
        first = answer_used_passage(POEM_ANSWER, PROVIDER_PASSAGE, POEM_QUERY)
        second = answer_used_passage(POEM_ANSWER, PROVIDER_PASSAGE, POEM_QUERY)
        self.assertEqual(first, second)
        self.assertFalse(first)


class _Resp:
    quality_passed = True
    escalated = False
    local = True
    tokens_prompt = 0
    tokens_completion = 0
    semantic_flags = ()

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeRetrieval:
    """Stands in for WikiRetrieval: always returns the same verified hit, which
    is exactly the condition under which the old code always cited."""

    def __init__(self, hit: WikiHit) -> None:
        self._hit = hit

    def retrieve(self, query, **kwargs):
        return self._hit


def _router_with_hit(answer_text: str) -> ConversationRouter:
    reg = ToolRegistry()
    reg.discover_tools()
    router = ConversationRouter(
        tool_registry=reg, semantic_matcher=SemanticMatcher(embedder=None),
        llm=LLMRouter(config=None), lock_dispatch=False)
    router._wiki_retrieval = _FakeRetrieval(WikiHit(
        rel_html="assistant/frontier-provider-setup.html",
        title="Frontier Provider Setup",
        passage=PROVIDER_PASSAGE,
        score=0.9,
        citation=CITATION,
    ))
    # No installed-tool grounding, so the wiki lookup is the path taken — the
    # same condition as the captured turn.
    router._grounding_context = lambda user_input: None
    router._llm.chat = lambda messages, **kw: _Resp(answer_text)
    router._screen_and_correct_claim = lambda text, *a, **k: text
    router._maybe_stage_generate_and_save = lambda *a, **k: None
    router._current_query_type = None
    return router


class FreeformTurnEmission(unittest.TestCase):
    """The router path the capture came from."""

    def test_an_answer_that_ignored_the_passage_carries_no_source_block(self):
        router = _router_with_hit(POEM_ANSWER)
        result = router._try_llm_freeform(POEM_QUERY)
        self.assertNotIn(
            "Source:", result.text,
            "the poem answered from the model's own weights; citing the "
            "retrieved page would claim a provenance it does not have")
        self.assertEqual(result.text.strip(), POEM_ANSWER.strip(),
                         "the answer itself still serves, unchanged")

    def test_an_answer_that_used_the_passage_still_cites_it(self):
        router = _router_with_hit(GROUNDED_ANSWER)
        result = router._try_llm_freeform(PROVIDER_QUERY)
        self.assertIn("Source:", result.text,
                      "withholding a citation from a genuinely grounded answer "
                      "would throw the feature away instead of fixing it")
        self.assertTrue(result.text.rstrip().endswith(CITATION))

    def test_the_citation_is_the_verified_line_retrieval_produced(self):
        # The gate decides WHETHER to cite; it never composes a citation of its
        # own, so the signed-manifest verification chain is still the only thing
        # that produces the line.
        router = _router_with_hit(GROUNDED_ANSWER)
        result = router._try_llm_freeform(PROVIDER_QUERY)
        self.assertIn(CITATION, result.text)


if __name__ == "__main__":
    unittest.main()
