# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
r"""The text taken out of a wiki page is bounded, and it never enters the
regular-expression engine.

WHAT WAS RECORDED. On 2026-09-20 a full test run on one of this project's
machines ended in a segmentation fault — exit 139, core dumped — at 89 percent
of the suite. The fault was reported inside the substitution that normalised
whitespace in the wiki page text extraction, over a whole rendered page. It
happened once in roughly twenty runs and did not come back in fourteen later
attempts, so nothing about it can be shown on demand.

WHAT CAN BE FIXED WITHOUT REPRODUCING IT. Two properties of that code were true
whether or not the crash ever returns, and both are checked here.

  1. THE INPUT WAS UNBOUNDED. Every verified page went into the extraction
     whole, however large, and the page text came out whole, however large. The
     installed wiki ships one page that is the entire book rendered as a single
     document, so the largest input grows with the wiki itself and nothing in
     the code said how large it was allowed to get. A ceiling that is never
     stated is a ceiling nobody can check. There is now a stated ceiling on the
     markup read and on the text produced; a page over it is cut at a word
     boundary and the cut is logged with the page's size and the limit, so a
     truncated page is never a silent one.

  2. THE NORMALISATION DID NOT NEED A REGULAR EXPRESSION AT ALL. Collapsing
     runs of whitespace to single spaces and trimming the ends is exactly what
     ``str.split()`` and ``" ".join`` do, in the interpreter's own string code.
     The two forms are proven character-for-character identical below, over
     every Unicode code point and over random whitespace strings. Doing it
     without the engine takes the reported crash site off this path entirely:
     a substitution that is never called cannot fault.

Neither property depends on the host, so every case here is built from strings
this file creates.
"""
from __future__ import annotations

import re
import unittest
from unittest import mock

from intergen import wiki_retrieval
from intergen.wiki_retrieval import html_to_text

# The ceilings are read from the module, with the values this file was written
# against as the fallback. They are read this way ON PURPOSE: a tree that
# declares no ceiling must fail these cases on the BEHAVIOUR it shows — an
# unbounded page coming back whole — and not merely on a missing name at import
# time, which would say nothing about what the code does.
_MAX_PAGE_HTML_CHARS = getattr(wiki_retrieval, "_MAX_PAGE_HTML_CHARS", 8_000_000)
_MAX_PAGE_TEXT_CHARS = getattr(wiki_retrieval, "_MAX_PAGE_TEXT_CHARS", 4_000_000)


class TheCeilingsAreDeclaredInTheCode(unittest.TestCase):
    """A limit that is not written down is a limit nobody can check."""

    def test_the_module_states_both_ceilings(self) -> None:
        self.assertTrue(hasattr(wiki_retrieval, "_MAX_PAGE_HTML_CHARS"),
                        "the module must state how much markup it will read "
                        "from one page")
        self.assertTrue(hasattr(wiki_retrieval, "_MAX_PAGE_TEXT_CHARS"),
                        "the module must state how much text it will produce "
                        "from one page")



class TheExtractedTextIsBounded(unittest.TestCase):
    """A page larger than the stated ceiling is cut, and the cut is announced."""

    def test_a_page_over_the_text_ceiling_is_cut_to_it(self) -> None:
        # One word per two characters, so the body is far over the ceiling
        # without being over the markup ceiling.
        body = "ab " * (_MAX_PAGE_TEXT_CHARS // 2)
        html = "<p>" + body + "</p>"
        self.assertLess(len(html), _MAX_PAGE_HTML_CHARS,
                        "this case must exercise the TEXT ceiling, not the "
                        "markup ceiling")
        with self.assertLogs(wiki_retrieval.logger, level="WARNING") as logged:
            text = html_to_text(html, source="oversized-page.html")
        self.assertLessEqual(len(text), _MAX_PAGE_TEXT_CHARS)
        said = "\n".join(logged.output)
        self.assertIn("oversized-page.html", said)
        self.assertIn(str(_MAX_PAGE_TEXT_CHARS), said)

    def test_a_page_over_the_markup_ceiling_is_read_only_to_it(self) -> None:
        html = "<p>" + ("word " * ((_MAX_PAGE_HTML_CHARS // 5) + 100)) + "</p>"
        self.assertGreater(len(html), _MAX_PAGE_HTML_CHARS)
        with self.assertLogs(wiki_retrieval.logger, level="WARNING") as logged:
            text = html_to_text(html, source="giant-page.html")
        self.assertLessEqual(len(text), _MAX_PAGE_TEXT_CHARS)
        said = "\n".join(logged.output)
        self.assertIn("giant-page.html", said)
        self.assertIn(str(_MAX_PAGE_HTML_CHARS), said)

    def test_the_cut_lands_on_a_word_boundary(self) -> None:
        html = "<p>" + ("alpha " * (_MAX_PAGE_TEXT_CHARS // 3)) + "</p>"
        with self.assertLogs(wiki_retrieval.logger, level="WARNING"):
            text = html_to_text(html, source="word-boundary.html")
        self.assertTrue(text.endswith("alpha"),
                        "a cut page must end on a whole word, not half of one")
        self.assertNotIn("  ", text)

    def test_a_page_within_the_ceiling_is_untouched_and_says_nothing(self) -> None:
        html = "<main><p>alpha</p><p>beta  gamma</p></main>"
        with mock.patch.object(wiki_retrieval.logger, "warning") as warned:
            self.assertEqual(html_to_text(html), "alpha beta gamma")
        warned.assert_not_called()

    def test_the_ceilings_clear_the_largest_page_the_project_ships(self) -> None:
        # Measured on the installed wiki on 2026-09-22: 88 verified pages, the
        # largest 994,504 characters of markup yielding 734,392 characters of
        # text. The ceilings must leave real room above that, or an ordinary
        # page starts being cut and the retrieval quietly loses material.
        self.assertGreaterEqual(_MAX_PAGE_HTML_CHARS, 994_504 * 4)
        self.assertGreaterEqual(_MAX_PAGE_TEXT_CHARS, 734_392 * 4)


class TheNormalisationDoesNotUseTheRegularExpressionEngine(unittest.TestCase):
    """The reported crash site is off this path, and the output is unchanged."""

    def test_the_text_comes_out_with_the_substitution_made_to_fail(self) -> None:
        html = "<main><p>alpha   beta</p>\n\t<li>gamma</li></main>"
        expected = html_to_text(html)
        self.assertEqual(expected, "alpha beta gamma")

        def refuse(*args: object, **kwargs: object) -> str:
            raise AssertionError(
                "the page text extraction called re.sub — the reported crash "
                "site is back on this path")

        with mock.patch.object(re, "sub", refuse):
            self.assertEqual(html_to_text(html), expected)

    def test_it_matches_the_substitution_on_every_unicode_code_point(self) -> None:
        def by_substitution(s: str) -> str:
            return re.sub(r"\s+", " ", s).strip()

        divergent = []
        for code_point in range(0x110000):
            sample = "a" + chr(code_point) + "b"
            if by_substitution(sample) != " ".join(sample.split()):
                divergent.append(hex(code_point))
        self.assertEqual(divergent, [],
                         "the two forms must agree on every code point")

    def test_it_matches_the_substitution_on_random_whitespace_strings(self) -> None:
        import random

        def by_substitution(s: str) -> str:
            return re.sub(r"\s+", " ", s).strip()

        space = [chr(c) for c in range(0x110000)
                 if re.match(r"\s", chr(c))] + ["a", "b"]
        rng = random.Random(20260920)
        for _ in range(5000):
            sample = "".join(rng.choice(space)
                             for _ in range(rng.randint(0, 40)))
            self.assertEqual(by_substitution(sample),
                             " ".join(sample.split()),
                             f"divergence on {sample!r}")


if __name__ == "__main__":
    unittest.main()
