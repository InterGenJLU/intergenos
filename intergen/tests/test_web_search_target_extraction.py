# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The rule that separates a framed web-search DISPATCH from a capability QUESTION.

``_web_search_target`` answers one question: does this sentence name something to look
up? It is the whole of the discrimination the router's two gates rely on, because the
shipped matcher resolves both groups to web_search — "can you search the web?" takes the
same keyword as "web search for the average price of a trilogy mill hill bead kit". Told
apart by recognition alone, the first would be answered by searching the web for the
words "search the web".

Every case is stated as (NORMALISED sentence -> target). The extractor runs on the
Layer-0 normalised form the matcher matched on, not on the raw sentence, and a table
written against raw sentences would be testing a path the product does not take.

The routing contract these cases serve is in test_router_executes_recognised_search.py.
"""

from __future__ import annotations

import unittest

from intergen.router import _web_search_target


class WebSearchTargetExtraction(unittest.TestCase):
    """The rule that separates a framed dispatch from a bare capability question.

    Every case is stated as (normalised sentence -> target), because the extractor runs
    on the Layer-0 normalised form the matcher matched on, not on the raw sentence.
    """

    NAMES_A_TARGET = {
        "web search and see how much a chippendale dining table sells for?":
            "how much a chippendale dining table sells for",
        "websearch to find that picture for me?": "picture",
        "web search for the average price of a trilogy mill hill bead kit":
            "the average price of a trilogy mill hill bead kit",
        "do a web search for the average price of a trilogy mill hill bead kit":
            "the average price of a trilogy mill hill bead kit",
        "yes, do a web search for the average price of a trilogy mill hill bead kit":
            "the average price of a trilogy mill hill bead kit",
        "search the web for how much a chippendale dining table sells for?":
            "how much a chippendale dining table sells for",
        "search online for cheap flights": "cheap flights",
        "google the melting point of tin": "the melting point of tin",
    }

    NAMES_NOTHING = [
        "search the web?",
        "search the internet?",
        "web search?",
        "go online?",
        "are you able to search the web",
        "you can't search the web?",
        "can't you search the web",
        "google it",
        "look it up",
        "look it up on the web",
        "do you have internet access?",
        "do you browse the internet?",
        "",
    ]

    def test_a_named_target_is_extracted(self):
        for sentence, target in self.NAMES_A_TARGET.items():
            with self.subTest(sentence=sentence):
                self.assertEqual(_web_search_target(sentence), target)

    def test_a_sentence_that_names_nothing_yields_none(self):
        for sentence in self.NAMES_NOTHING:
            with self.subTest(sentence=sentence):
                self.assertIsNone(
                    _web_search_target(sentence),
                    f"{sentence!r} names nothing to look up, so it must not be read as "
                    f"a dispatch — it is a question about the capability.")


if __name__ == "__main__":
    unittest.main()
