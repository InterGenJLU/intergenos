# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A curated subject grounds only a question that carries its keyword as a word.

The reference index chose a subject by a keyword found anywhere in the lowered
question, and its facts are consulted before the installed wiki on both the
freeform and the tool route. Over the 155-question evaluation corpus and four
questions about the assistant, eight chose a subject only by a keyword inside
another word or in a feature's name: "tar" in "start" and "restart", "word" in
"password", "phone" in "microphone" and in "Phone-A-Friend". Asked what the
wiki says about Phone-A-Friend, the model was handed the phone-media facts and
no wiki passage, and answered that Phone-A-Friend is not mentioned.

THE TEST THAT FINDS THIS CLASS: each of the eight questions grounds on no
subject, and the controls — a keyword as a word, as a plural, and the padded
"lp " keyword at the end of a question — still ground on theirs.
"""
from __future__ import annotations

import pytest

from intergen import reference
from intergen.reference import ReferenceIndex

MISMATCHED = (
    "how do I start a service",
    "how do I enable a service to start at boot",
    "how do I restart a service",
    "how do I make a program start automatically when I log in",
    "how do I change my password",
    "how do I change the root password",
    "my microphone isn't working",
    "what does the wiki say about Phone-A-Friend",
)
#: (question, the subject it names by a whole word)
CONTROLS = (
    ("how do I get photos off my phone", "phone photos / external media"),
    ("how do I change the default application for pdfs",
     "document / PDF conversion"),
    ("can I print it with lp", "printing"),
)


def _subject(question: str) -> str | None:
    """The subject lookup's choice, read back from the block it returns."""
    block = ReferenceIndex().lookup(question)
    if block is None:
        return None
    return block.splitlines()[0].removeprefix("Subject: ")


@pytest.mark.parametrize("question", MISMATCHED)
def test_a_keyword_inside_another_word_grounds_nothing(question):
    assert _subject(question) is None


@pytest.mark.parametrize("question,subject", CONTROLS)
def test_a_keyword_that_stands_as_a_word_still_grounds(question, subject):
    assert _subject(question) == subject


def test_every_keyword_matches_itself_as_a_word():
    """No keyword is written so that it can never match."""
    from intergen.reference import keyword_in
    for cap in reference._CAPABILITIES:
        for kw in cap.keywords:
            assert keyword_in(kw, f"about {kw.strip()} here"), kw
