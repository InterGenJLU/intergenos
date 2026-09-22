# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
r"""The text taken out of a wiki page is bounded, it ends on a whole word, and
its whitespace is not collapsed by a regular-expression substitution.

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
     markup read and on the text produced. What a page over either ceiling
     keeps is the first words of the page's text, each one whole, in order:
     the markup ceiling can fall inside a word, a tag, a character reference,
     a comment or an attribute value, and no part of any of those may reach
     the index as text. A page with no word boundary under a ceiling keeps
     nothing. Every cut is logged with the page, its size, the limit and the
     length kept, so a truncated page is never a silent one.

  2. THE NORMALISATION DID NOT NEED A REGULAR EXPRESSION AT ALL. Collapsing
     runs of whitespace to single spaces and trimming the ends is exactly what
     ``str.split()`` and ``" ".join`` do, in the interpreter's own string code.
     The two forms are proven character-for-character identical below, over
     every Unicode code point and over random whitespace strings. Collapsing
     it with string methods takes the reported crash site, that substitution
     over a whole page, off this path: a substitution that is never called
     cannot fault. The normalisation calls nothing in the regular-expression
     engine, and a profiler below checks that; the standard HTML tokenizer the
     extraction is built on still uses the engine to find tags, which is not
     what this file claims about.

Neither property depends on the host. Every case here is built from strings
this file creates, except the two that read this tree's own source files to
check that each caller names the page it passes in.
"""
from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path
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


class TheMarkupCeilingKeepsOnlyWholeWords(unittest.TestCase):
    """Over the MARKUP ceiling, what is kept ends on a whole word of the page.

    The markup is cut at a character position, which can fall inside a word,
    inside a tag or inside a character reference. The shapes below were
    measured on 2026-09-22: a page of repeated units, with the markup ceiling
    set so that the first character left out falls at a chosen offset inside
    a unit. The ceiling is lowered in-process so each case is small."""

    UNIT = '<span class="w">abcdefgh</span> '   # 32 characters, 9 of them text

    def _text_under_a_markup_ceiling(self, html: str, ceiling: int) -> str:
        with mock.patch.object(wiki_retrieval, "_MAX_PAGE_HTML_CHARS", ceiling), \
                self.assertLogs(wiki_retrieval.logger, level="WARNING"):
            return html_to_text(html, source="markup-cut.html")

    def _assert_only_whole_units_are_kept(self, offset: int) -> None:
        html = self.UNIT * 10
        text = self._text_under_a_markup_ceiling(html, len(self.UNIT) * 5 + offset)
        self.assertTrue(text, "the words before the cut must be kept")
        self.assertEqual(set(text.split()), {"abcdefgh"},
                         f"a cut at offset {offset} of a unit kept {text[-24:]!r}")
        self.assertNotIn("<", text)

    def test_a_cut_inside_a_word_keeps_no_part_of_that_word(self) -> None:
        self.assertEqual(self.UNIT[16:20], "abcd")
        self._assert_only_whole_units_are_kept(20)

    def test_a_cut_inside_a_closing_tag_keeps_none_of_its_characters(self) -> None:
        self.assertEqual(self.UNIT[24:26], "</")
        self._assert_only_whole_units_are_kept(26)

    def test_a_cut_inside_an_opening_tag_keeps_whole_words(self) -> None:
        # The control: a cut here gave whole words before this correction too.
        self.assertEqual(self.UNIT[:8], "<span cl")
        self._assert_only_whole_units_are_kept(8)

    def test_a_word_continued_after_an_inline_tag_is_not_kept_in_part(self) -> None:
        html = "<p>alpha beta abc<b>def</b> gamma</p>"
        ceiling = html.index("<b>d") + len("<b>d")
        self.assertEqual(self._text_under_a_markup_ceiling(html, ceiling),
                         "alpha beta")

    def test_every_cut_position_keeps_whole_words_from_the_start(self) -> None:
        # Words, entities, an attribute holding an escaped '>', a comment,
        # block and inline tags, a script holding markup, a numeric reference.
        page = ('<html><head><title>t</title></head><body><nav>menu</nav>'
                '<main><h1>Disk encryption</h1><p>Fish &amp; chips, caf&eacute; '
                'and a <a href="x.html" title="a&gt;b">link text</a>.</p>'
                '<!-- a comment --><ul><li>one</li><li>two<em>three</em></li></ul>'
                '<script>var x = "<p>not text</p>";</script>'
                '<p>tail&#x41;word end</p></main></body></html>')
        whole = html_to_text(page).split()
        self.assertEqual(len(whole), 14)
        for ceiling in range(1, len(page)):
            kept = self._text_under_a_markup_ceiling(page, ceiling).split()
            self.assertEqual(kept, whole[:len(kept)],
                             f"a cut after {page[:ceiling][-24:]!r} kept "
                             f"{kept[-2:]!r}, which are not whole words of "
                             f"the page in order")

    def test_a_page_exactly_at_the_markup_ceiling_is_whole_and_says_nothing(self) -> None:
        html = self.UNIT * 10
        with mock.patch.object(wiki_retrieval, "_MAX_PAGE_HTML_CHARS", len(html)), \
                mock.patch.object(wiki_retrieval.logger, "warning") as warned:
            text = html_to_text(html)
        self.assertEqual(text.split(), ["abcdefgh"] * 10)
        warned.assert_not_called()


class NoTextFromACommentOrAttributeValueOpenAtTheCut(unittest.TestCase):
    """Over the MARKUP ceiling, nothing the whole page holds inside a comment
    or an attribute value is kept as text.

    The tokenizer ends two constructs differently when their closing markup
    lies past the cut: a comment opened as ``<!-->`` or ``<!--->`` with its
    ``-->`` further on, and a quoted attribute value with whitespace or a
    quote mark just before its equals sign, or whitespace just after it.
    Measured on 2026-09-22: cut markup of either shape made the words inside
    them text. The pages below carry line breaks, so a cut also falls on a
    later line than the one the open construct began on."""

    FILLER_UNIT = '<span class="w">abcdefgh</span> '

    def _kept(self, html: str, ceiling: int) -> list[str]:
        with mock.patch.object(wiki_retrieval, "_MAX_PAGE_HTML_CHARS", ceiling), \
                self.assertLogs(wiki_retrieval.logger, level="WARNING"):
            return html_to_text(html, source="open-at-the-cut.html").split()

    def _assert_every_cut_keeps_the_first_words(self, page: str,
                                                whole: list[str]) -> None:
        self.assertEqual(html_to_text(page).split(), whole)
        for ceiling in range(1, len(page)):
            kept = self._kept(page, ceiling)
            self.assertEqual(kept, whole[:len(kept)],
                             f"a cut after {page[:ceiling][-24:]!r} kept "
                             f"{kept[-3:]!r}, which are not the page's first "
                             f"words in order")

    def test_every_cut_of_a_comment_opened_as_empty_keeps_none_of_it(self) -> None:
        for opener in ("<!-->", "<!--->"):
            with self.subTest(opener=opener):
                page = ("<main><p>one two</p>\n" + opener + "three\nfour five"
                        "<p>six</p>-->\n<p>seven eight</p></main>")
                self._assert_every_cut_keeps_the_first_words(
                    page, ["one", "two", "seven", "eight"])

    def test_every_cut_of_an_open_quoted_value_keeps_none_of_it(self) -> None:
        shapes = {
            "whitespace before the equals sign":
                '<a title\n="x> three four five">link</a>',
            "whitespace after the equals sign":
                '<a title= "x>\nthree four five">link</a>',
            "single quotes, spaces around the equals sign":
                "<a title = 'x> three four five'>link</a>",
            "a name that ends in a quote mark":
                '<a b"="x> three four five">link</a>',
            "an end tag carrying such a value":
                '<a>link</a title ="x> three four five">',
        }
        for shape, markup in shapes.items():
            with self.subTest(shape=shape):
                page = ("<main><p>one two</p>\n" + markup
                        + "\n<p>six seven</p></main>")
                self._assert_every_cut_keeps_the_first_words(
                    page, ["one", "two", "link", "six", "seven"])

    def test_a_quoted_value_with_no_space_at_its_equals_sign(self) -> None:
        # The control: this spacing ends the same way at every cut, and did
        # before this correction too.
        page = ('<main><p>one two</p>\n<a title="x> three four five">link</a>'
                "\n<p>six seven</p></main>")
        self._assert_every_cut_keeps_the_first_words(
            page, ["one", "two", "link", "six", "seven"])

    def _at_the_real_ceiling(self, opener: str, closer: str) -> None:
        head = "<main><p>"
        units = (_MAX_PAGE_HTML_CHARS - len(head) - 200) // len(self.FILLER_UNIT)
        page = (head + self.FILLER_UNIT * units + "</p>" + opener
                + "hidden " * 100 + closer + "<p>" + "after " * 50
                + "</p></main>")
        opened = page.index(opener)
        self.assertLess(opened, _MAX_PAGE_HTML_CHARS)
        self.assertGreater(page.index(closer, opened + len(opener)),
                           _MAX_PAGE_HTML_CHARS,
                           "the construct must still be open at the ceiling")
        with self.assertLogs(wiki_retrieval.logger, level="WARNING"):
            kept = html_to_text(page, source="real-ceiling.html").split()
        self.assertNotIn("hidden", kept,
                         "text the whole page holds inside the construct "
                         "was kept")
        self.assertEqual(kept, ["abcdefgh"] * (units - 1))

    def test_a_comment_open_at_the_real_ceiling_keeps_none_of_it(self) -> None:
        for opener in ("<!-->", "<!--->"):
            with self.subTest(opener=opener):
                self._at_the_real_ceiling(opener, "-->")

    def test_a_quoted_value_open_at_the_real_ceiling_keeps_none_of_it(self) -> None:
        self._at_the_real_ceiling('<a title ="x> ', '">link</a>')


class TheTextCeilingKeepsOnlyWholeWords(unittest.TestCase):
    """Over the TEXT ceiling, the longest run of whole words that fits is kept."""

    def _text_under_a_text_ceiling(self, html: str, ceiling: int) -> str:
        with mock.patch.object(wiki_retrieval, "_MAX_PAGE_TEXT_CHARS", ceiling), \
                self.assertLogs(wiki_retrieval.logger, level="WARNING"):
            return html_to_text(html, source="text-cut.html")

    def test_a_ceiling_at_the_end_of_a_word_keeps_that_word(self) -> None:
        self.assertEqual(
            self._text_under_a_text_ceiling("<p>alpha beta gamma</p>", 10),
            "alpha beta")

    def test_text_with_no_word_boundary_under_the_ceiling_keeps_nothing(self) -> None:
        self.assertEqual(
            self._text_under_a_text_ceiling("<p>" + "x" * 11 + "</p>", 10), "")

    def test_every_ceiling_keeps_the_longest_whole_word_run_that_fits(self) -> None:
        words = "alpha be gamma delta e zeta".split()
        full = " ".join(words)
        for ceiling in range(1, len(full)):
            text = self._text_under_a_text_ceiling("<p>" + full + "</p>", ceiling)
            kept = text.split()
            self.assertEqual(kept, words[:len(kept)])
            self.assertLessEqual(len(text), ceiling)
            self.assertGreater(len(" ".join(words[:len(kept) + 1])), ceiling,
                               f"at a ceiling of {ceiling} one more whole word "
                               f"fits than the {len(kept)} kept")


class EachCutIsLoggedWithTheLengthKept(unittest.TestCase):
    """The log line says how much of the page was kept, not only the limit."""

    @staticmethod
    def _numbers_in(line: str) -> set[int]:
        return {int(n) for n in re.findall(r"\d+", line)}

    def test_the_text_ceiling_line_states_the_length_kept(self) -> None:
        html = "<p>alpha beta gamma delta epsilon</p>"      # 30 characters of text
        with mock.patch.object(wiki_retrieval, "_MAX_PAGE_TEXT_CHARS", 20), \
                self.assertLogs(wiki_retrieval.logger, level="WARNING") as logged:
            text = html_to_text(html, source="kept-length.html")
        self.assertEqual(text, "alpha beta gamma")
        # The kept length differs from the ceiling and from the size, so a line
        # that states only those two cannot pass.
        self.assertNotIn(len(text), (20, 30))
        (line,) = logged.output
        self.assertIn("kept-length.html", line)
        self.assertIn(len(text), self._numbers_in(line))

    def test_the_markup_ceiling_line_states_the_length_kept(self) -> None:
        html = "<p>" + "alpha beta gamma " * 4 + "</p>"
        ceiling = 40
        with mock.patch.object(wiki_retrieval, "_MAX_PAGE_HTML_CHARS", ceiling), \
                self.assertLogs(wiki_retrieval.logger, level="WARNING") as logged:
            text = html_to_text(html, source="kept-length.html")
        self.assertTrue(text)
        self.assertNotIn(len(text), (ceiling, len(html)))
        (line,) = logged.output
        self.assertIn("kept-length.html", line)
        self.assertIn(len(text), self._numbers_in(line))

    def test_both_lines_state_the_final_length_when_both_ceilings_cut(self) -> None:
        html = "<p>" + "alpha " * 20 + "</p>"
        with mock.patch.object(wiki_retrieval, "_MAX_PAGE_HTML_CHARS", 60), \
                mock.patch.object(wiki_retrieval, "_MAX_PAGE_TEXT_CHARS", 20), \
                self.assertLogs(wiki_retrieval.logger, level="WARNING") as logged:
            text = html_to_text(html, source="both-ceilings.html")
        self.assertEqual(text, "alpha alpha alpha")
        self.assertEqual(len(logged.output), 2)
        for line in logged.output:
            self.assertIn(len(text), self._numbers_in(line))

    def test_a_page_that_keeps_nothing_says_so(self) -> None:
        with mock.patch.object(wiki_retrieval, "_MAX_PAGE_TEXT_CHARS", 10), \
                self.assertLogs(wiki_retrieval.logger, level="WARNING") as logged:
            text = html_to_text("<p>" + "x" * 11 + "</p>", source="one-word.html")
        self.assertEqual(text, "")
        (line,) = logged.output
        self.assertIn("one-word.html", line)
        self.assertIn(0, self._numbers_in(line))
        self.assertIn("never indexed", line)


class EveryCallerNamesThePage(unittest.TestCase):
    """A cut is logged with the page's name. A caller that passes none makes
    the line say "(unnamed)" although the page's name is in scope."""

    _SCRIPT = (Path(__file__).resolve().parents[2]
               / "scripts" / "wiki-citation-calibration.py")

    def _assert_every_call_passes_the_page_name(self, path: Path) -> None:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and getattr(node.func, "id", getattr(node.func, "attr", None))
                 == "html_to_text"]
        self.assertTrue(calls, f"{path.name} no longer calls html_to_text")
        for call in calls:
            self.assertIn("source", {kw.arg for kw in call.keywords},
                          f"{path.name} line {call.lineno} does not name the page")

    def test_the_index_build_names_the_page(self) -> None:
        self._assert_every_call_passes_the_page_name(Path(wiki_retrieval.__file__))

    def test_the_calibration_script_names_the_page(self) -> None:
        # This file is shipped into the installed package, where the
        # repository's scripts/ is not beside it; in a checkout it is.
        if not self._SCRIPT.is_file():
            self.skipTest("repository scripts/ not present (installed layout)")
        self._assert_every_call_passes_the_page_name(self._SCRIPT)


class TheNormalisationDoesNotUseTheRegularExpressionEngine(unittest.TestCase):
    """The reported crash site is off this path, and the output is unchanged."""

    @staticmethod
    def _engine_calls_while(run: object) -> list[str]:
        """Every call into the regular-expression engine made while ``run``
        runs: a Python-level call into the re package, or a call on a compiled
        pattern or a match object. Patching ``re.sub`` alone misses a pattern
        compiled once and used directly."""
        seen: list[str] = []

        def profile(frame: object, event: str, arg: object) -> None:
            if event == "call":
                module = frame.f_globals.get("__name__", "")
                if module == "re" or module.startswith("re."):
                    seen.append(f"{module}.{frame.f_code.co_name}")
            elif event == "c_call":
                owner = getattr(arg, "__self__", None)
                if isinstance(owner, (re.Pattern, re.Match)):
                    seen.append(f"{type(owner).__name__}.{arg.__name__}")
                elif getattr(arg, "__module__", None) in ("re", "_sre"):
                    seen.append(f"{arg.__module__}.{arg.__name__}")

        previous = sys.getprofile()
        sys.setprofile(profile)
        try:
            run()
        finally:
            sys.setprofile(previous)
        return seen

    def test_the_instrument_sees_a_compiled_pattern_and_a_module_call(self) -> None:
        # The positive control: the check below must be able to fail.
        pattern = re.compile(r"\s+")
        self.assertIn("Pattern.sub",
                      self._engine_calls_while(lambda: pattern.sub(" ", "a  b")))
        self.assertTrue(
            self._engine_calls_while(lambda: re.sub(r"\s+", " ", "a  b")))

    def test_the_normalisation_makes_no_call_into_the_engine(self) -> None:
        parser = wiki_retrieval._WikiTextExtractor()
        parser.feed("<main><p>alpha   beta</p>\n\t<li>gamma &amp; delta</li></main>")
        parser.close()
        self.assertEqual(self._engine_calls_while(parser.text), [])
        self.assertEqual(parser.text(), "alpha beta gamma & delta")

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
