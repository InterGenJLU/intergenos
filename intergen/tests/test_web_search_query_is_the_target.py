# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""What is SENT to the web-search tool is the thing to look up, not the sentence.

THE DEFECT, at intergen/router.py in _extract_arguments:

    if tool_name == "web_search":
        return {"query": user_input}

The whole raw sentence goes to the tool as the search query. So a person who types
"can you web search and see how much a chippendale dining table sells for?" causes
a search for that entire sentence — question mark, politeness, "can you", and all —
rather than for the dining table. A search engine given a sentence of framing
returns results about the framing.

The router ALREADY KNOWS the answer. _recognised_web_dispatch (router.py:2689) is
what decides this turn is a web search at all, and the way it decides is by asking
_web_search_target (router.py:758) what the sentence names. It removes, in order, a
leading affirmative or filler run, the search verb phrase, any connective filler,
and trailing politeness, and returns what is left — or None when the residue is all
framing, which is what keeps "can you search the web?" answered from the capability
surface instead of being dispatched as a search for its own wording. That extracted
target is returned, used to decide the dispatch, and then thrown away; the argument
builder starts again from the raw sentence.

WHAT THIS FILE PINS. The query string handed to the tool, read AT THE REGISTRY
BOUNDARY with execution stubbed — no search leaves this machine. When the sentence
names a target, that target is the query. When it names none, the sentence is the
query, unchanged: a caller that reaches the argument builder by some other path
than the web dispatch must not be handed an empty string.
"""

from __future__ import annotations

import unittest

from intergen.router import ConversationRouter, _web_search_target
from intergen.semantic import SemanticMatcher

# (the sentence a person typed, what should be looked up).
# The first is the measured field sentence; the rest are the same shape with the
# framing varied, so a fix that special-cases one wording fails here.
NAMED_TARGETS = (
    ("can you web search and see how much a chippendale dining table sells for?",
     "how much a chippendale dining table sells for"),
    ("web search for the average price of a trilogy mill hill bead kit",
     "the average price of a trilogy mill hill bead kit"),
    ("please look up the boiling point of nitrogen",
     "the boiling point of nitrogen"),
    ("search the web for tide times at aberdeen tomorrow",
     "tide times at aberdeen tomorrow"),
)

# Sentences that name NOTHING to look up. _web_search_target returns None for these,
# and the query must then be the sentence itself — unchanged and never empty.
NAMES_NOTHING = (
    "can you search the web?",
    "do you have web search?",
)


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        r = ConversationRouter.__new__(ConversationRouter)
        r._semantic = SemanticMatcher(embedder=None)
        self.r = r

    def _query_for(self, sentence: str) -> str:
        args = self.r._extract_arguments("web_search", sentence)
        self.assertIsNotNone(args, f"no arguments built for {sentence!r}")
        self.assertIn("query", args)
        return args["query"]


class TheQueryIsTheExtractedTargetTests(_Base):
    def test_a_named_target_is_what_is_searched_for(self):
        for sentence, target in NAMED_TARGETS:
            with self.subTest(sentence=sentence):
                self.assertEqual(
                    self._query_for(sentence), target,
                    "the whole sentence was sent to the search tool instead of "
                    "the thing it asked to look up")

    def test_the_framing_is_gone_from_the_query(self):
        """Named separately from the equality above: a future extractor that
        returns a different but still framing-free string would keep this true,
        and it is the property that actually matters to a search engine."""
        for sentence, _target in NAMED_TARGETS:
            with self.subTest(sentence=sentence):
                q = self._query_for(sentence).lower()
                for framing in ("can you", "please", "web search", "search the web",
                                "look up", "?"):
                    self.assertNotIn(
                        framing, q,
                        f"the query still carries the framing {framing!r}")


class NothingNamedKeepsTheSentenceTests(_Base):
    """The control: the argument builder is reachable independently of the web
    dispatch, so it must never hand the tool an empty or invented query."""

    def test_a_sentence_naming_nothing_is_passed_through(self):
        for sentence in NAMES_NOTHING:
            with self.subTest(sentence=sentence):
                self.assertIsNone(
                    _web_search_target(
                        self.r._semantic._normalize_input(sentence)),
                    "control: this sentence is supposed to name nothing")
                self.assertEqual(self._query_for(sentence), sentence)

    def test_the_query_is_never_empty(self):
        for sentence in [s for s, _ in NAMED_TARGETS] + list(NAMES_NOTHING):
            with self.subTest(sentence=sentence):
                self.assertTrue(self._query_for(sentence).strip())


class NoSearchLeavesTheBoxTests(_Base):
    """Execution is stubbed by construction here: _extract_arguments only BUILDS
    the argument dict and never calls the tool. This pins that, so a later change
    that made argument extraction perform the search would fail rather than
    quietly start sending traffic from a unit run."""

    def test_extracting_arguments_dispatches_nothing(self):
        called = []
        self.r._tools = type("_Reg", (), {
            "execute_tool": lambda _s, *a, **k: called.append(a)})()
        for sentence, _t in NAMED_TARGETS:
            self.r._extract_arguments("web_search", sentence)
        self.assertEqual(called, [],
                         "argument extraction executed a tool")


if __name__ == "__main__":
    unittest.main()
