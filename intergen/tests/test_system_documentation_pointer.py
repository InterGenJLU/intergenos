# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An uncovered system how-to gets an honest documentation pointer."""

import time
from unittest.mock import Mock

import pytest

from intergen.howto import HowtoCorpus
from intergen.state_cache import CachedValue, StateCache
from intergen.tests.howto_content_helpers import DATA
from intergen.tests.test_wiki_citation_provenance import (
    GROUNDED_ANSWER, PROVIDER_QUERY, _Resp, _router_with_hit,
)


UNCOVERED = "How do I enable a hypothetical frobnicator in InterGenOS?"
NORMAL_ANSWER = "A normal answer to the question."


def router_without_documentation():
    router = _router_with_hit(NORMAL_ANSWER)
    router._wiki_retrieval = Mock()
    router._wiki_retrieval.retrieve.return_value = None
    router._llm.chat = Mock(return_value=_Resp(NORMAL_ANSWER))
    return router


def test_uncovered_system_howto_gets_a_pointer_without_a_model_guess():
    assert HowtoCorpus(data_dir=DATA, embedder=None).retrieve(UNCOVERED)[0] is None
    router = router_without_documentation()
    result = router._try_llm_freeform(UNCOVERED)
    assert "I don't have documentation for that" in result.text
    assert "/usr/share/doc/intergenos/wiki/index.html" in result.text
    assert "https://wiki.intergenos.org/" in result.text
    assert "Source:" not in result.text
    assert result.handled is True
    assert result.used_llm is False
    assert result.answer_linkage.kind == "code"
    router._wiki_retrieval.retrieve.assert_called_once_with(UNCOVERED)
    router._llm.chat.assert_not_called()


@pytest.mark.parametrize("decide_only", [False, True])
@pytest.mark.parametrize("query", [
    "How do I bake bread?",
    "How do I write a Python script to list packages?",
    "How do I code a package manager?",
    "Thanks, I installed the package.",
    "Write me a script that renames my photos by date.",
])
def test_other_turns_keep_the_existing_freeform_path(query, decide_only):
    router = router_without_documentation()
    result = router._try_llm_freeform(query, decide_only=decide_only)
    if decide_only:
        assert result.text == ""
        assert result.handled is False
        assert result.source == "llm_freeform"
        router._llm.chat.assert_not_called()
    else:
        assert result.text == NORMAL_ANSWER
        assert result.used_llm is True
        router._llm.chat.assert_called_once()


def test_a_verified_passage_still_reaches_the_model():
    router = _router_with_hit(GROUNDED_ANSWER)
    router._llm.chat = Mock(return_value=_Resp(GROUNDED_ANSWER))
    result = router._try_llm_freeform(PROVIDER_QUERY)
    assert "I don't have documentation for that" not in result.text
    assert "Source:" in result.text
    router._llm.chat.assert_called_once()


def test_installed_tool_grounding_is_not_replaced_with_a_pointer():
    router = router_without_documentation()
    router._grounding_context = Mock(return_value="Verified installed-tool facts.")
    result = router._try_llm_freeform(UNCOVERED)
    assert result.text == NORMAL_ANSWER
    router._wiki_retrieval.retrieve.assert_not_called()
    router._llm.chat.assert_called_once()


@pytest.mark.parametrize("decide_only", [False, True])
def test_live_state_question_uses_its_existing_cache_route(decide_only):
    router = router_without_documentation()
    cache = StateCache()
    cache._cache["kernel"] = CachedValue(
        value="6.18.10-test", timestamp=time.monotonic(),
        command="fixture", stale_after=60,
    )
    router._state_cache = cache
    router._tools.execute = Mock(side_effect=AssertionError("cache must answer"))
    with router.bind_conversation(router.new_conversation()):
        result = router.route("what kernel am I running?", decide_only=decide_only)
    assert "6.18.10-test" in result.text
    assert result.source == "cache"
    router._llm.chat.assert_not_called()
    router._wiki_retrieval.retrieve.assert_not_called()


def test_streaming_decision_serves_the_same_uncovered_howto_pointer():
    router = router_without_documentation()
    router._tools.execute = Mock(side_effect=AssertionError("how-to is not an action"))
    with router.bind_conversation(router.new_conversation()):
        result = router.route(UNCOVERED, decide_only=True)
    assert "I don't have documentation for that" in result.text
    assert result.handled is True
    assert result.source == "wiki_uncovered"
    router._llm.chat.assert_not_called()
    router._tools.execute.assert_not_called()


@pytest.mark.parametrize(("query", "locked", "source"), [
    ("How do I bake bread?", False, "llm_freeform"),
    ("Tell me about winter.", False, "llm_tools"),
    ("Tell me about winter.", True, "llm_freeform"),
])
def test_streaming_general_decision_still_defers_generation(query, locked, source):
    router = router_without_documentation()
    router._lock_dispatch = locked
    with router.bind_conversation(router.new_conversation()):
        result = router.route(query, decide_only=True)
    assert result.source == source
    assert result.handled is False
    assert result.text == ""
    router._llm.chat.assert_not_called()


@pytest.mark.parametrize("decide_only", [False, True])
def test_instructional_clause_uses_the_same_documentation_boundary(decide_only):
    router = router_without_documentation()
    with router.bind_conversation(router.new_conversation()):
        result = router._route_single(
            UNCOVERED, trail_scope="sub_query:2", decide_only=decide_only)
    assert "I don't have documentation for that" in result.text
    assert result.source == "wiki_uncovered"
    assert result.handled is True
    router._llm.chat.assert_not_called()
