# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The teaching retriever ranks the requested subject ahead of generic words."""
from pathlib import Path
from unittest import mock

import pytest

from intergen.howto import HowtoCorpus, STRONG_THRESHOLD


@pytest.fixture
def corpus():
    return HowtoCorpus(data_dir=Path(__file__).resolve().parents[1] / "data" / "howto")


@pytest.mark.parametrize("question,entry_id,command", [
    ("how do I check for updates", "pkm-update-system", "pkm upgrade"),
    ("how do I see which packages are installed?", "pkm-info-list", "pkm list"),
    ("I just installed InterGenOS. how do I check for updates",
     "pkm-update-system", "pkm upgrade"),
])
def test_keyword_candidate_answers_the_requested_package_subject(corpus, question, entry_id, command):
    entry, score = corpus.retrieve(question)
    assert entry is not None, score
    assert entry.id == entry_id
    assert command in entry.answer
    assert entry.domain == "pkm"


@pytest.mark.parametrize("score,accepted", [(0.757, False), (STRONG_THRESHOLD, True)])
def test_foreign_embedding_candidate_needs_a_strong_match(corpus, score, accepted):
    installer = next(e for e in corpus._entries if e.id == "igos-forge-install")
    with mock.patch.object(corpus, "_embeddings", object()), \
            mock.patch.object(corpus, "_retrieve_embedding",
                              return_value=(installer, score, False)):
        entry, _ = corpus.retrieve("how do I check for updates")
    assert (entry is not None) is accepted


@pytest.mark.parametrize("question", [
    "how do I update the system?", "how do I list installed packages",
    "how do I install software", "how do I start a service",
    "how do I use the installer",
])
def test_existing_concrete_howto_keeps_a_candidate(corpus, question):
    entry, _ = corpus.retrieve(question)
    assert entry is not None
