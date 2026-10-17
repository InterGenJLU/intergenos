# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Weak documentation matches respect the public-address qualifier."""
from unittest import mock

import pytest

from intergen.howto import HowtoEntry
from intergen.tests.test_howto_subject_candidates import corpus
from intergen.tests.test_compound_howto_routing import router, entry, embedding_choice


@pytest.mark.parametrize("query", [
    "how do I find my public ip", "how do I find my external IP address",
    "how do I check my public IPv4 address", "how do I see my external IPv6 address",
])
@pytest.mark.parametrize("score,accepted", [(0.757, False), (0.95, True)])
def test_weak_interface_match_needs_the_public_address_qualifier(corpus, query, score, accepted):
    local = next(e for e in corpus._entries if e.id == "net-status")
    with mock.patch.object(corpus, "_embeddings", object()), \
            mock.patch.object(corpus, "_retrieve_embedding", return_value=(local, score, False)):
        result, _ = corpus.retrieve(query)
    assert (result is not None) is accepted


def test_an_explicit_external_address_lesson_is_still_eligible(corpus):
    external = HowtoEntry(
        id="external-address", domain="networking",
        triggers=("how do I find my external IP address",),
        answer="A public address is observed by an external service.",
        doc_source="networking")
    with mock.patch.object(corpus, "_embeddings", object()), \
            mock.patch.object(corpus, "_retrieve_embedding", return_value=(external, 0.757, False)):
        result, _ = corpus.retrieve("how do I find my public ip")
    assert result is external


def test_an_unqualified_interface_question_still_has_its_lesson(corpus):
    local = next(e for e in corpus._entries if e.id == "net-status")
    with mock.patch.object(corpus, "_embeddings", object()), \
            mock.patch.object(corpus, "_retrieve_embedding", return_value=(local, 0.757, False)):
        result, _ = corpus.retrieve("how do I see my IP address")
    assert result is local


def test_public_address_question_refuses_the_recorded_document_route(router):
    with embedding_choice(router, entry(router, "net-status"), 0.757):
        result = router.route("how do I find my public ip", decide_only=True)
    assert result.source == "llm_freeform"
    assert not result.handled
    router._llm.chat.assert_not_called()
    router._llm.stream_with_tools.assert_not_called()
    router._tools.execute.assert_not_called()


@pytest.mark.parametrize("context", [
    "I just installed Firefox.", "I just checked my public IP.",
])
def test_introductory_context_does_not_replace_the_instruction_subject(corpus, context):
    result, _ = corpus.retrieve(context + " how do I check for updates")
    assert result is not None
    assert result.id == "pkm-update-system"
