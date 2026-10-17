# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Compound teaching keeps every clause and weak matches share a subject.

The captured whole-question embedding choice exercises the real router,
decomposition and corpus thresholds. Clause candidates come from the shipped
corpus's real keyword retriever. No model or tool is allowed to execute during
a teaching turn; live embedding selection is measured separately.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
from unittest import mock

import pytest

from intergen.howto import HowtoCorpus
from intergen.interfaces.types import HardwareTierLevel, RouteResult
from intergen.intents import register_all_intents
from intergen.llm import LLMRouter
from intergen.router import ConversationRouter
from intergen.semantic import SemanticMatcher
from intergen.tool_registry import ToolRegistry


CORPUS = Path(__file__).resolve().parents[1] / "data" / "howto"
COMPOUND = "how do I check for updates, and how do I see which packages are installed?"
CONTEXTUAL = "I just installed InterGenOS. " + COMPOUND


@pytest.fixture
def router():
    registry = ToolRegistry()
    registry.discover_tools()
    registry.execute = mock.Mock(side_effect=AssertionError("unexpected dispatch"))
    matcher = SemanticMatcher(embedder=None)
    register_all_intents(matcher)
    llm = LLMRouter(config=None)
    llm.chat = mock.Mock(side_effect=AssertionError("unexpected model call"))
    llm.stream_with_tools = mock.Mock(side_effect=AssertionError("unexpected model call"))
    with mock.patch.dict(os.environ, {"INTERGEN_HOWTO_DIR": str(CORPUS)}):
        result = ConversationRouter(
            tool_registry=registry, semantic_matcher=matcher, llm=llm,
            hardware_tier=HardwareTierLevel.TIER_2, lock_dispatch=False)
    result._howto = HowtoCorpus(embedder=None, data_dir=CORPUS)
    result._current_query_type = "general"
    return result


def entry(router, entry_id):
    return next(e for e in router._howto._entries if e.id == entry_id)


@contextmanager
def embedding_choice(router, chosen, score):
    """Exercise real loose/strong thresholds without a model server."""
    corpus = router._howto
    with mock.patch.object(corpus, "_embeddings", object()), \
            mock.patch.object(corpus, "_retrieve_embedding",
                              return_value=(chosen, score, False)):
        yield


@pytest.mark.parametrize("question", [COMPOUND, CONTEXTUAL])
def test_both_instructions_answered_after_the_captured_installer_match(router, question):
    corpus = router._howto
    retrieve = corpus.retrieve
    installer = entry(router, "igos-forge-install")

    def recorded_choice(query, **kwargs):
        if "and how" in query.lower():
            with embedding_choice(router, installer, 0.757392168045044):
                return retrieve(query, **kwargs)
        return retrieve(query, **kwargs)

    with mock.patch.object(corpus, "retrieve", side_effect=recorded_choice):
        answer = router.route(question)
    assert answer.source == "decomposed", answer.text
    assert "pkm sync" in answer.text
    assert "pkm upgrade" in answer.text
    assert "pkm list" in answer.text
    assert "Forge" not in answer.text
    assert not answer.used_llm
    router._tools.execute.assert_not_called()


@pytest.mark.parametrize("question", [COMPOUND, CONTEXTUAL])
def test_keyword_retrieval_answers_both_instructions_without_a_replayed_choice(router, question):
    answer = router.route(question)
    assert answer.source == "decomposed"
    assert "pkm sync" in answer.text
    assert "pkm upgrade" in answer.text
    assert "pkm list" in answer.text
    assert not answer.used_llm
    router._tools.execute.assert_not_called()








@pytest.mark.parametrize("question", [
    "how do I update the system?", "how do I update my system",
    "how do I list installed packages",
])
def test_existing_single_instruction_still_teaches(router, question):
    answer = router.route(question)
    assert answer.source == "explain"
    assert "pkm" in answer.text
    router._tools.execute.assert_not_called()


def test_shared_instructional_prefix_does_not_turn_the_second_clause_into_an_action(router):
    answer = router.route("how do I update my system and list installed packages")
    assert answer.source == "decomposed"
    assert "pkm upgrade" in answer.text
    assert "pkm list" in answer.text
    router._tools.execute.assert_not_called()


def test_an_uncovered_instructional_clause_stays_a_question(router):
    fallback = RouteResult(text="An explanation.", source="llm_freeform",
                           handled=True, used_llm=True)
    with mock.patch.object(router, "_try_llm_freeform", return_value=fallback) as model:
        answer = router._route_single("how do I calibrate the frobnicator")
    assert answer is fallback
    model.assert_called_once()
    router._tools.execute.assert_not_called()


def test_a_decomposed_howto_clause_reaches_the_documentation_answer(router):
    fallback = RouteResult(text="A model answer.", source="llm_freeform",
                           handled=True, used_llm=True)
    with mock.patch.object(router, "_try_llm_freeform", return_value=fallback):
        answer = router._route_single("how do I list installed packages")
    assert answer.source == "explain"
    assert "pkm list" in answer.text
    router._tools.execute.assert_not_called()


@pytest.mark.parametrize("verb", ["reboot", "shutdown"])
def test_shared_howto_prefix_includes_a_power_command_without_executing_it(router, verb):
    explanation = RouteResult(text=f"To {verb}, use `{verb}`.", source="llm_freeform",
                              handled=True, used_llm=True)
    with mock.patch.object(router, "_try_llm_freeform", return_value=explanation):
        answer = router.route("how do I update my system and " + verb)
    assert answer.source == "decomposed"
    assert "pkm upgrade" in answer.text
    assert verb in answer.text
    router._tools.execute.assert_not_called()


def test_disabled_teaching_data_does_not_turn_a_clause_into_an_action(router):
    router._howto = None
    explanation = RouteResult(text="An explanation.", source="llm_freeform",
                              handled=True, used_llm=True)
    with mock.patch.object(router, "_try_llm_freeform", return_value=explanation):
        answer = router._route_single("how do I install an application")
    assert answer is explanation
    router._tools.execute.assert_not_called()
