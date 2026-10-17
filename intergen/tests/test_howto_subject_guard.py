# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Weak documentation matches must share the question's subject.

Retrieval candidates are stood in below the real corpus threshold selection,
including the keyword fallback used when an embedding request fails.
"""
from pathlib import Path
from unittest import mock

import pytest

from intergen.howto import HowtoCorpus, STRONG_THRESHOLD
from intergen.router import ConversationRouter
from intergen.semantic import SemanticMatcher


@pytest.fixture
def router():
    result = ConversationRouter.__new__(ConversationRouter)
    result._turn_index = None
    result._semantic = SemanticMatcher(embedder=None)
    result._howto = HowtoCorpus(
        data_dir=Path(__file__).resolve().parents[1] / "data" / "howto")
    result._reference = None
    result._pending_action_offer = None
    result._conversation_history = []
    result._max_history = 20
    return result


def choose(router, entry_id, score, *, keyword=False):
    corpus = router._howto
    entry = next(e for e in corpus._entries if e.id == entry_id)
    return mock.patch.object(corpus, "_retrieve_embedding",
                             return_value=(entry, score, keyword))


@pytest.mark.parametrize("question", [
    "how do I check for updates", "how do I check for system updates",
    "how do I see which packages are installed?",
])
def test_weak_installer_match_does_not_answer_a_package_question(router, question):
    with mock.patch.object(router._howto, "_embeddings", object()), \
            choose(router, "igos-forge-install", 0.757):
        answer, prior = router._try_explain(question)
    assert prior
    assert answer is None
    assert router._pending_action_offer is None
    assert router._conversation_history == []


@pytest.mark.parametrize("question,entry_id", [
    ("how do I check for updates", "pkm-update-system"),
    ("how do I inspect installed packages", "pkm-info-list"),
    ("how do I use the installer", "igos-forge-install"),
])
def test_weak_shared_subject_still_teaches(router, question, entry_id):
    with mock.patch.object(router._howto, "_embeddings", object()), \
            choose(router, entry_id, 0.757):
        answer, prior = router._try_explain(question)
    assert prior
    assert answer is not None


def test_strong_match_keeps_a_paraphrase_without_literal_overlap(router):
    with mock.patch.object(router._howto, "_embeddings", object()), \
            choose(router, "pkm-update-system", STRONG_THRESHOLD):
        answer, prior = router._try_explain("how do I stay current")
    assert prior
    assert answer is not None
    assert "pkm upgrade" in answer.text


@pytest.mark.parametrize("outage", [False, True])
def test_keyword_strong_threshold_is_used_even_after_an_embedding_outage(router, outage):
    corpus = router._howto
    chosen = next(e for e in corpus._entries if e.id == "pkm-update-system")
    with mock.patch.object(corpus, "_embeddings", object() if outage else None), \
            mock.patch.object(corpus, "_retrieve_keyword", return_value=(chosen, 0.70)), \
            choose(router, "pkm-update-system", 0.70, keyword=True):
        answer, _ = router._try_explain("how do I stay current")
    assert answer is not None


def test_keyword_weak_foreign_subject_is_refused(router):
    corpus = router._howto
    chosen = next(e for e in corpus._entries if e.id == "igos-forge-install")
    with mock.patch.object(corpus, "_retrieve_keyword", return_value=(chosen, 0.50)):
        answer, _ = router._try_explain("how do I check for updates")
    assert answer is None
