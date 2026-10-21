# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An uncovered system how-to gets an honest documentation pointer."""

import time
from unittest.mock import Mock

import pytest

from intergen.howto import HowtoCorpus
from intergen.router import ConversationRouter
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


def router_with_source_corpus(locked):
    router = router_without_documentation()
    router._howto = HowtoCorpus(data_dir=DATA, embedder=None)
    router._lock_dispatch = locked
    router._grounding_context = Mock(return_value=None)
    router._tools.execute = Mock(side_effect=AssertionError("no tool may execute"))
    return router


@pytest.mark.parametrize("query", [
    "How do I train a service dog?",
    "How do I import packages in Python?",
    "How do I bake bread?",
])
@pytest.mark.parametrize("locked", [False, True])
@pytest.mark.parametrize("decide_only", [False, True])
def test_non_system_howto_retains_existing_generation(query, locked, decide_only):
    router = router_with_source_corpus(locked)
    assert router._howto.retrieve(query)[0] is None
    with router.bind_conversation(router.new_conversation()):
        result = router.route(query, decide_only=decide_only)
    assert result.source == "llm_freeform", (query, result.source, result.text)
    assert result.handled is not decide_only
    assert result.text == ("" if decide_only else NORMAL_ANSWER)
    assert router._llm.chat.call_count == (0 if decide_only else 1)
    router._tools.execute.assert_not_called()


@pytest.mark.parametrize("query", [
    "How do I train my service dog?",
    "How do I roast a corn kernel?",
    "How do I import packages in Python on my computer?",
])
@pytest.mark.parametrize("locked", [False, True])
@pytest.mark.parametrize("decide_only", [False, True])
def test_ambiguous_words_do_not_establish_a_system_subject(query, locked, decide_only):
    router = router_with_source_corpus(locked)
    assert router._howto.retrieve(query)[0] is None
    with router.bind_conversation(router.new_conversation()):
        result = router.route(query, decide_only=decide_only)
    assert result.source == "llm_freeform", (query, result.source, result.text)
    assert result.text == ("" if decide_only else NORMAL_ANSWER)
    assert router._llm.chat.call_count == (0 if decide_only else 1)
    router._tools.execute.assert_not_called()


@pytest.mark.parametrize("query", [
    "How do I draw the Linux penguin?",
    "How do I parse Linux logs in Rust?",
    "How do I draw the InterGenOS logo on my computer?",
    "How do I parse Linux logs in Go?",
])
@pytest.mark.parametrize("locked", [False, True])
@pytest.mark.parametrize("decide_only", [False, True])
def test_platform_mentions_in_art_and_code_keep_generation(query, locked, decide_only):
    router = router_with_source_corpus(locked)
    assert router._howto.retrieve(query)[0] is None
    with router.bind_conversation(router.new_conversation()):
        result = router.route(query, decide_only=decide_only)
    assert result.source == "llm_freeform", (query, result.source, result.text)
    assert result.handled is not decide_only
    assert result.text == ("" if decide_only else NORMAL_ANSWER)
    assert router._llm.chat.call_count == (0 if decide_only else 1)
    router._tools.execute.assert_not_called()


@pytest.mark.parametrize("query", [
    "How do I change the colors of the Linux penguin in a drawing?",
    "How do I adjust a drawing on my computer?",
    "How do I change the colors of a drawing on Linux?",
])
@pytest.mark.parametrize("locked", [False, True])
@pytest.mark.parametrize("decide_only", [False, True])
def test_an_administrative_verb_does_not_replace_the_requested_object(query, locked, decide_only):
    router = router_with_source_corpus(locked)
    assert router._howto.retrieve(query)[0] is None
    with router.bind_conversation(router.new_conversation()):
        result = router.route(query, decide_only=decide_only)
    assert result.source == "llm_freeform", (query, result.source, result.text)
    assert result.handled is not decide_only
    assert result.text == ("" if decide_only else NORMAL_ANSWER)
    assert router._llm.chat.call_count == int(not decide_only)
    router._tools.execute.assert_not_called()


def test_a_removal_instruction_still_requires_a_system_object():
    query = "How do I remove an element from a Linux penguin illustration?"
    assert ConversationRouter._system_documentation_pointer(query) is None


@pytest.mark.parametrize("query", [
    "How do I change a hypothetical feature in Linux?",
    "How do I adjust Linux desktop settings?",
    "How do I remove a custom system package?",
    "How do I check my firewall?",
    "How do I open GNOME desktop settings?",
    "How do I change the colors of the Linux desktop?",
    "How do I change the hostname in Linux?",
    "How do I list all files on this computer?",
    "How do I show active processes on Linux?",
    "How do I adjust the display resolution in Linux?",
    "How do I change the timezone on this computer?",
    "How do I check disk usage in Linux?",
])
def test_general_actions_with_system_objects_keep_the_pointer(query):
    assert ConversationRouter._system_documentation_pointer(query) is not None


@pytest.mark.parametrize("query", [
    "How do I temporarily disable a hypothetical feature in InterGenOS?",
    "What command enables a hypothetical feature in Linux?",
    "How do I turn on a hypothetical feature in InterGenOS?",
])
@pytest.mark.parametrize("locked", [False, True])
@pytest.mark.parametrize("decide_only", [False, True])
def test_administration_modifiers_inflections_and_phrasal_verbs_keep_documentation(query, locked, decide_only):
    router = router_with_source_corpus(locked)
    assert router._howto.retrieve(query)[0] is None
    with router.bind_conversation(router.new_conversation()):
        result = router.route(query, decide_only=decide_only)
    assert result.source == "wiki_uncovered", (query, result.source, result.text)
    assert result.handled is True
    assert result.answer_linkage.renderer == "wiki_uncovered"
    router._llm.chat.assert_not_called()
    router._tools.execute.assert_not_called()


@pytest.mark.parametrize("query", [
    "What command changes the colors of the Linux penguin in a drawing?",
    "How do I temporarily adjust a drawing on my computer?",
])
def test_instruction_variants_keep_the_same_object_boundary(query):
    assert ConversationRouter._system_documentation_pointer(query) is None


@pytest.mark.parametrize("query", [
    "How do I import packages in R on my computer?",
    "How do I import packages in Julia on my computer?",
])
@pytest.mark.parametrize("locked", [False, True])
@pytest.mark.parametrize("decide_only", [False, True])
def test_language_imports_with_computer_context_keep_generation(query, locked, decide_only):
    router = router_with_source_corpus(locked)
    assert router._howto.retrieve(query)[0] is None
    with router.bind_conversation(router.new_conversation()):
        result = router.route(query, decide_only=decide_only)
    assert result.source == "llm_freeform", (query, result.source, result.text)
    assert result.handled is not decide_only
    assert result.text == ("" if decide_only else NORMAL_ANSWER)
    assert router._llm.chat.call_count == (0 if decide_only else 1)
    router._tools.execute.assert_not_called()


@pytest.mark.parametrize("query", [
    "Show me how to enable a hypothetical feature in Linux.",
    "Explain how to configure a hypothetical feature on my computer.",
    "Walk me through configuring a hypothetical feature in InterGenOS.",
    "How would I go about configuring a hypothetical feature in InterGenOS?",
    "What command can I use to configure a hypothetical feature in Linux?",
    "What's the right way to configure a hypothetical feature on this computer?",
    "How does Linux work?",
])
def test_system_administration_instruction_forms_keep_the_pointer(query):
    assert ConversationRouter._system_documentation_pointer(query) is not None


@pytest.mark.parametrize(("query", "source"), [
    (UNCOVERED, "wiki_uncovered"),
    ("How do I enable a hypothetical systemd service?", "explain"),
    ("How do I configure a hypothetical GNOME extension?", "wiki_uncovered"),
    ("How do I configure a hypothetical feature on this computer?", "wiki_uncovered"),
])
@pytest.mark.parametrize("locked", [False, True])
@pytest.mark.parametrize("decide_only", [False, True])
def test_system_subjects_keep_their_documentation_route(query, source, locked, decide_only):
    router = router_with_source_corpus(locked)
    assert (router._howto.retrieve(query)[0] is None) is (source == "wiki_uncovered")
    with router.bind_conversation(router.new_conversation()):
        result = router.route(query, decide_only=decide_only)
    assert result.source == source, (query, result.source, result.text)
    assert result.handled is True
    if source == "wiki_uncovered":
        assert "/usr/share/doc/intergenos/wiki/index.html" in result.text
        assert result.answer_linkage.renderer == "wiki_uncovered"
    else:
        assert "systemctl enable" in result.text
        assert result.answer_linkage.renderer == "howto_corpus"
    router._llm.chat.assert_not_called()
    router._tools.execute.assert_not_called()


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


@pytest.mark.parametrize("query", [UNCOVERED, "How do I open a terminal?"])
def test_completed_streaming_decision_records_the_answered_turn(query):
    router = router_without_documentation()
    router._record = Mock()
    with router.bind_conversation(router.new_conversation()):
        result = router.route(query, decide_only=True)
    assert result.handled and result.text
    router._record.assert_called_once()
    assert router._record.call_args.args[0] is result


def test_normal_pointer_records_once_without_a_second_inner_record():
    router = router_without_documentation()
    router._record = Mock()
    with router.bind_conversation(router.new_conversation()):
        result = router.route(UNCOVERED)
    assert result.source == "wiki_uncovered"
    router._record.assert_called_once()
    assert router._record.call_args.args[0] is result


def test_compound_pointer_records_the_combined_answer_once():
    router = router_without_documentation()
    router._record = Mock()
    query = "How do I open a terminal and how do I enable a hypothetical frobnicator in InterGenOS?"
    with router.bind_conversation(router.new_conversation()):
        result = router.route(query, decide_only=True)
    assert result.handled and result.source == "decomposed"
    assert "I don't have documentation for that" in result.text
    router._record.assert_called_once()
    assert router._record.call_args.args[0] is result
