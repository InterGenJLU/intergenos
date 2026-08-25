# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Tests for the fail-closed public-repo language gate (scripts/check-public-language.py).

Rule 22 / build-rules Rule I enforcement. These tests use ONLY SYNTHETIC tokens
generated here at run time — never a real internal identifier — because test
fixtures are committed to the public tree and must themselves be clean. The
synthetic tokens ("ZQXSEATA", …) are coined here and carry no meaning outside
this file.

Proves: a bare token as a word blocks; the same letters inside a larger word do
not; a Co-Authored-By trailer passes; an authorized product model-stack ref
passes; a real hit on a line that also carries an exempt token STILL blocks
(span-aware); a missing / empty term list fails CLOSED; and end-to-end range
scanning catches an added file line and a commit-message line.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "check-public-language.py"

_spec = importlib.util.spec_from_file_location("check_public_language", _SCRIPT)
clg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(clg)

# Synthetic banned tokens — coined here, not real identifiers.
TOK = "ZQXSEATA"        # a distinctive synthetic token
SHORT = "QZ"            # a synthetic short/collision-prone token


def _compiled(*terms):
    return clg.compile_terms(list(terms))


class MatchingTests(unittest.TestCase):
    def test_bare_token_as_word_hits(self):
        ct = _compiled(TOK)
        self.assertEqual(clg.scan_line(f"note ({TOK} finding): x", ct), [TOK])

    def test_short_token_bare_hits(self):
        # The evasion class: a bare short token as a standalone word must hit,
        # even with no "per <tok>" phrasing around it.
        ct = _compiled(SHORT)
        self.assertEqual(clg.scan_line(f"Adjudicated ({SHORT} A-X2): ...", ct), [SHORT])

    def test_substring_inside_larger_word_does_not_hit(self):
        # Word-boundary: the token's letters inside a longer word are not a hit.
        ct = _compiled(SHORT)
        self.assertEqual(clg.scan_line(f"the {SHORT}RANK metric and {SHORT}abc id", ct), [])

    def test_trailer_line_passes_whole(self):
        ct = _compiled(TOK)
        self.assertEqual(
            clg.scan_line(f"Co-Authored-By: {TOK} <x@y.z>", ct), [],
            "a Co-Authored-By trailer is an authorized home and passes whole-line")

    def test_intergen_model_stack_ref_passes(self):
        # The InterGen product model-stack exemption is real (in-code, public).
        ct = _compiled("InternVL")  # even if the product model name were listed
        self.assertEqual(clg.scan_line("InterGen runs InternVL3.5-2B for vision", ct), [])

    def test_span_aware_real_hit_blocks_despite_exempt_token_on_line(self):
        # A legitimate exempt token on the SAME line must not launder a real hit.
        ct = _compiled(TOK)
        self.assertEqual(
            clg.scan_line(f"InternVL swap reviewed by {TOK} last week", ct), [TOK])

    def test_technical_collision_exempt_mechanism(self):
        # The in-code collision-exempt mechanism: a token inside a documented
        # exempt span is suppressed, proven by injecting a synthetic exempt
        # pattern (the mechanism, not a specific real collision).
        ct = _compiled(SHORT)
        synthetic_exempt = [("synthetic", re.compile(rf"{SHORT}-v\d+"))]
        with mock.patch.object(clg, "_EXEMPT_SPAN_PATTERNS", synthetic_exempt):
            self.assertEqual(clg.scan_line(f"the {SHORT}-v2 driver", ct), [],
                             "token inside an exempt span is suppressed")
            self.assertEqual(clg.scan_line(f"{SHORT} reviewed the {SHORT}-v2 driver", ct),
                             [SHORT], "a bare token outside the span still blocks")

    def test_posix_word_count_command_span(self):
        # The documented word-count-command collision (2026-07-15): the utility
        # in command position with its flag is exempt; the bare letters in
        # prose still block. The token is assembled at runtime so this public
        # test file never carries it as a standalone word.
        tok = "w" + "c"
        ct = _compiled(tok)
        self.assertEqual(clg.scan_line(f"ls /var/lib/pkgs | {tok} -l", ct), [],
                         "the word-count command with a flag is an exempt span")
        self.assertEqual(clg.scan_line(f"{tok} delivered the review", ct), [tok],
                         "the bare letters outside command position still block")

    def test_audio_codec_twin_package_and_path_element_span(self):
        # The documented audio-codec collision (2026-08-05): the codec's 32-bit
        # twin PACKAGE NAME and the codec as a SLASH-DELIMITED element are
        # exempt, because a hyphen and a slash both satisfy the boundary
        # matcher while the text is a package/path identifier, not prose. The
        # capitalised spelling in prose must STILL block — that spelling is how
        # a model attribution would appear, and carving it would blind the gate
        # to the very class it exists to catch. Both tokens are assembled at
        # run time so this public test file never carries either as a
        # standalone word.
        low = "o" + "pus"
        cap = "O" + "pus"
        ct = _compiled(low)
        self.assertEqual(
            clg.scan_line(f"### lib32-{low} (1.6.1)", ct), [],
            "the 32-bit twin package name is an exempt span")
        self.assertEqual(
            clg.scan_line(f"# (libsndfile -> flac/vorbis/{low} -> ogg)", ct), [],
            "a slash-delimited element of a dependency chain is an exempt span")
        self.assertEqual(
            clg.scan_line(f"Reviewed by {cap} 5 before the push", ct), [cap],
            "the capitalised spelling in prose still blocks — the carve is "
            "case-sensitive so model attribution stays caught")
        self.assertEqual(
            clg.scan_line(f"### lib32-{low} (1.6.1) - reviewed by {cap} 5", ct), [cap],
            "an exempt package name on the line does not launder a real hit")

    def test_audio_codec_single_entry_covers_the_shapes_that_used_to_block(self):
        """The four position-anchored codec carves became one stated rule.

        The four never converged: measured against the tracked tree, five more
        shapes were still uncarved and would have blocked the next edit that
        touched them. Each is asserted here so a future narrowing cannot drop
        one silently. Both tokens are assembled at run time so this public test
        file never carries either as a standalone word.
        """
        low = "o" + "pus"
        ct = _compiled(low)
        for shape in (
            f"### {low} (1.6.1)",                        # versioned section heading
            f"name: {low}",                              # recipe key-value
            f"  - pkg_config: {low}",                    # curation key-value
            f"lib32_source: {low}",                      # 32-bit twin's source key
            f"| {low} | 1.6.1 | none | Audio codec |",   # table cell
            f"110. [desktop] {low} 1.6.1 | deps: none",  # build-tier listing
            f"[optional   ] [INTRA] {low}",              # audit listing element
            f"theora, vorbis, ogg, lame, {low}, speex and vpx.",   # prose enumeration
            f"igos_helper_record_dep {low}",             # shell helper argument
            f"- Homepage: https://{low}-codec.org/",     # upstream homepage host
            f"### lib32-{low} (1.6.1)",                  # 32-bit twin package name
            f"# (libsndfile -> flac/vorbis/{low} -> ogg)",         # path element
            f"  - {low}                 # optional",     # dependency-list entry
        ):
            self.assertEqual(clg.scan_line(shape, ct), [],
                             f"the codec package name must be exempt in: {shape}")

    def test_audio_codec_capitalised_form_is_carved_only_beside_codec_words(self):
        """The asymmetry that keeps model attribution blocked.

        The lowercase spelling is the package name and is exempt anywhere. The
        capitalised spelling is exempt ONLY immediately beside codec-domain
        vocabulary, which is how the notices file and the package descriptions
        write it — so an attribution, which is written capitalised and without
        that vocabulary, still blocks.
        """
        low = "o" + "pus"
        cap = "O" + "pus"
        ct = _compiled(low)
        for exempt in (
            f"High-level decoder for the standard {cap}-in-Ogg container format",
            f"- {low}file             # {cap} container/decoder, optional",
            f"{cap} codec, optional",
            f"{cap} audio at 48 kHz",
            f"the {cap} encoder path",
        ):
            self.assertEqual(clg.scan_line(exempt, ct), [],
                             f"the capitalised form beside codec vocabulary is exempt: {exempt}")
        for blocked in (
            f"Reviewed by {cap} 5 before the push",
            f"drafted with {cap}",
            f"{cap} produced the first version of this file",
        ):
            self.assertEqual(clg.scan_line(blocked, ct), [cap],
                             f"a capitalised attribution must still block: {blocked}")

    def test_licence_identifier_suffix_carve_matches_its_comment(self):
        """The licence-suffix carve names its licence stems instead of accepting
        any capitalised word.

        Its comment said "only the suffix position of a licence identifier is
        exempt" while its pattern read `[A-Z][\\w.+]*` — any capitalised token
        at all. The tightened form is a list of the stems the tracked tree
        actually carries; anything else is a hit again. The token is assembled
        at run time so this public test file never carries it as a standalone
        word.
        """
        tok = "U" + "C"
        ct = _compiled(tok)
        for exempt in (
            "- License: `BSD-4-Clause-" + tok + "`",
            '    "HPND-' + tok + '",',
            '    "HPND-' + tok + '-export-US",',
            "license: BSD-4-Clause-" + tok,
        ):
            self.assertEqual(clg.scan_line(exempt, ct), [],
                             f"a named licence identifier stays exempt: {exempt}")
        for blocked in (
            "Reviewed-" + tok + " signed off on the change",
            "Handoff-" + tok + " is not a licence identifier",
            "the " + tok + " reviewed it",
        ):
            self.assertEqual(clg.scan_line(blocked, ct), [tok],
                             f"a non-licence capitalised stem must block again: {blocked}")


class SeparatorVariantTests(unittest.TestCase):
    """A multi-token entry blocks all three spellings of its separator.

    The bypass this closes: an entry listed in ONE spelling ("a-b") passed when
    the same two tokens were written with the other separator ("a b") or run
    together ("ab"). All tokens here are synthetic and coined in this file.
    """

    HYPHENATED = f"{TOK}-{SHORT}"
    SPACED = f"{TOK} {SHORT}"

    def _variants(self, first, second):
        return (f"note {first}-{second} here",
                f"note {first} {second} here",
                f"note {first}{second} here")

    def test_hyphenated_entry_blocks_all_three_spellings(self):
        ct = _compiled(self.HYPHENATED)
        for line in self._variants(TOK, SHORT):
            self.assertTrue(clg.scan_line(line, ct),
                            f"a separator variant must block: {line}")

    def test_spaced_entry_blocks_all_three_spellings(self):
        ct = _compiled(self.SPACED)
        for line in self._variants(TOK, SHORT):
            self.assertTrue(clg.scan_line(line, ct),
                            f"a separator variant must block: {line}")

    def test_separator_run_blocks(self):
        # A run of separators is the same evasion as one character.
        ct = _compiled(self.HYPHENATED)
        for line in (f"note {TOK}  {SHORT} here",
                     f"note {TOK} - {SHORT} here",
                     f"note {TOK}--{SHORT} here"):
            self.assertTrue(clg.scan_line(line, ct),
                            f"a separator run must block: {line}")

    def test_word_boundary_still_holds_across_the_rejoin(self):
        # The rejoined form is still boundary-anchored: the same letters inside
        # a larger word are not a hit.
        ct = _compiled(self.HYPHENATED)
        for line in (f"the {TOK}{SHORT}X driver", f"the X{TOK}-{SHORT} driver",
                     f"the {TOK}{SHORT}ing pass"):
            self.assertEqual(clg.scan_line(line, ct), [],
                             f"a larger word must not hit: {line}")

    def test_unlisted_separators_are_not_matched(self):
        # NAMED RESIDUE, pinned so a future widening is a deliberate choice:
        # an underscore, a dot and a case-join carry no variant and do not hit.
        ct = _compiled(self.HYPHENATED)
        for line in (f"note {TOK}_{SHORT} here", f"note {TOK}.{SHORT} here"):
            self.assertEqual(clg.scan_line(line, ct), [],
                             f"an unlisted separator must not hit: {line}")

    def test_single_token_entry_matcher_is_unchanged(self):
        # Every one-word entry keeps the exact previous matcher.
        self.assertEqual(clg.term_pattern_body(TOK), re.escape(TOK))
        ct = _compiled(TOK)
        self.assertEqual(clg.scan_line(f"a {TOK} b", ct), [TOK])
        self.assertEqual(clg.scan_line(f"a {TOK}X b", ct), [])

    def test_entry_edged_with_a_separator_falls_back_to_exact_spelling(self):
        # A term that begins or ends with a separator keeps its exact escaped
        # spelling — that character is part of the term as written.
        for edged in (f"-{TOK}", f"{TOK}-", f" {TOK}", f"{TOK} "):
            self.assertEqual(clg.term_pattern_body(edged), re.escape(edged),
                             f"an edge-separator term must not be rejoined: {edged!r}")

    def test_exemption_ladder_still_applies_to_a_rejoined_match(self):
        # A rejoined hit inside a documented exempt span is still suppressed,
        # and a rejoined hit outside one still blocks.
        ct = _compiled(self.HYPHENATED)
        synthetic_exempt = [("synthetic", re.compile(rf"{TOK} {SHORT}-v\d+"))]
        with mock.patch.object(clg, "_EXEMPT_SPAN_PATTERNS", synthetic_exempt):
            self.assertEqual(clg.scan_line(f"the {TOK} {SHORT}-v2 driver", ct), [])
            self.assertTrue(clg.scan_line(f"{TOK}{SHORT} in the {TOK} {SHORT}-v2 driver", ct))

    def test_trailer_line_still_passes_whole_for_a_rejoined_match(self):
        ct = _compiled(self.HYPHENATED)
        self.assertEqual(clg.scan_line(f"Co-Authored-By: {TOK} {SHORT} <x@y.z>", ct), [])


class FailClosedTests(unittest.TestCase):
    def test_missing_list_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "does-not-exist"
            with self.assertRaises(clg.ListUnavailable):
                clg.load_terms(missing)

    def test_empty_list_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            empty = Path(td) / "list"
            empty.write_text("# only comments\n\n   \n")
            with self.assertRaises(clg.ListUnavailable):
                clg.load_terms(empty)

    def test_comments_and_blanks_ignored_terms_loaded(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "list"
            p.write_text(f"# header\n\n{TOK}\n  {SHORT}  \n")
            self.assertEqual(clg.load_terms(p), [TOK, SHORT])


class RangeScanTests(unittest.TestCase):
    def _git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, check=True,
                       capture_output=True, text=True)

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.repo = Path(self._td.name)
        self._git("init", "-q")
        self._git("config", "user.email", "t@t")
        self._git("config", "user.name", "t")
        (self.repo / "f.txt").write_text("clean baseline line\n")
        self._git("add", "f.txt")
        self._git("commit", "-qm", "base")

    def tearDown(self):
        self._td.cleanup()

    def test_added_line_and_commit_message_both_caught(self):
        # An added file line carrying a synthetic token + a commit message that
        # also carries it — both must surface as violations in the range.
        (self.repo / "f.txt").write_text(
            f"clean baseline line\nnew line reviewed by {TOK}\n")
        self._git("add", "f.txt")
        self._git("commit", "-qm", f"change touched by {TOK} in the message")
        ct = _compiled(TOK)
        violations = clg.scan_range("HEAD~1..HEAD", ct, self.repo)
        locs = [loc for loc, _ in violations]
        self.assertTrue(any("f.txt" in l for l in locs), f"file line missed: {locs}")
        self.assertTrue(any("commit" in l for l in locs), f"commit msg missed: {locs}")

    def test_clean_range_passes(self):
        (self.repo / "f.txt").write_text("clean baseline line\nanother clean line\n")
        self._git("add", "f.txt")
        self._git("commit", "-qm", "a clean neutral change")
        ct = _compiled(TOK)
        self.assertEqual(clg.scan_range("HEAD~1..HEAD", ct, self.repo), [])


if __name__ == "__main__":
    unittest.main()


class DigitSuffixTests(unittest.TestCase):
    """A coined seat identifier followed by digits escaped the gate.

    Measured 2026-08-24 with a three-line probe: a name written as
    ``<name>-canary-x`` hit, ``<name>093-evidence/…`` did NOT, bare ``<name>``
    hit. The trailing ``(?!\\w)`` treats a digit as part of the word, so the
    name-plus-digits shape reached a public remote in thirteen commit messages
    and one branch name before a person caught it.

    Widening EVERY term this way is wrong — it was measured against the whole
    tree and fired on ``hd0,gpt2`` (a partition specifier), ``sol2`` (a
    library) and ``SOL_SOCKET``. So the list marks the terms that get the
    widened boundary with a trailing ``*``: for those, letters and underscore
    still end the match, digits do not. Unmarked terms keep the strict
    boundary. The star is list syntax, never part of the term.
    """

    def test_starred_term_hits_with_digit_suffix(self):
        ct = _compiled(f"{TOK}*")
        # The report names the matched text — the term — not the digits after it.
        self.assertEqual(clg.scan_line(f"see {TOK}093-evidence/x", ct), [TOK])

    def test_starred_term_still_hits_bare_and_hyphenated(self):
        ct = _compiled(f"{TOK}*")
        self.assertEqual(clg.scan_line(f"{TOK}-canary-x", ct), [TOK])
        self.assertEqual(clg.scan_line(f"bare {TOK} here", ct), [TOK])

    def test_starred_term_does_not_hit_inside_a_longer_word(self):
        ct = _compiled(f"{TOK}*")
        self.assertEqual(clg.scan_line(f"{TOK}RANK and {TOK}_socket and 9{TOK}", ct), [])

    def test_unstarred_term_keeps_the_strict_boundary(self):
        ct = _compiled(TOK)
        self.assertEqual(clg.scan_line(f"see {TOK}093-evidence/x", ct), [])

    def test_star_is_syntax_not_term_text(self):
        # The literal star is never matched and never reported.
        ct = _compiled(f"{TOK}*")
        self.assertEqual(clg.scan_line(f"{TOK}* literal", ct), [TOK])

    def test_text_mode_end_to_end_with_starred_list(self):
        # The pre-push ref-name gate goes through --text; a seat-named ref
        # with a digit suffix must be refused there too.
        with tempfile.TemporaryDirectory() as td:
            lst = Path(td) / "list"
            lst.write_text(f"# synthetic\n{TOK}*\n", encoding="utf-8")
            r = subprocess.run(
                ["python3", str(_SCRIPT), "--denylist", str(lst),
                 "--label", "ref name", "--text", f"{TOK.lower()}093/topic"],
                capture_output=True, text=True, timeout=120)
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)


class WrappedTermTests(unittest.TestCase):
    """A term split by a line wrap must be seen.

    Measured 2026-08-25 against the real term list: the gate matched each added
    line on its own, so a term whose spelling is two words passed whenever the
    author's editor put the wrap between its words. A probe carrying the same
    term twice — once wrapped, once on one line — reported one hit.

    The synthetic term here is coined in this file and means nothing outside it.
    The counterpart test over the REAL list is below and builds its fixtures
    from that list at run time, so no term is ever written into this tree.
    """

    TWO_WORD = "ZQXSEAT" + " " + "ALPHA"

    def test_term_wrapped_between_its_words_is_a_hit(self):
        ct = _compiled(self.TWO_WORD)
        run = [(10, "the change was reviewed by the ZQXSEAT"),
               (11, "ALPHA lane before it landed")]
        found = clg.scan_run(run, ct)
        self.assertEqual(len(found), 1, found)
        lineno, text, _hit = found[0]
        self.assertEqual(lineno, 10, "the report names the line the match starts on")
        self.assertIn("reviewed by", text)

    def test_single_line_hit_is_unchanged(self):
        ct = _compiled(self.TWO_WORD)
        self.assertEqual(clg.scan_line(f"reviewed by {self.TWO_WORD} last week", ct),
                         [self.TWO_WORD])

    def test_two_innocent_words_wrapped_do_not_hit(self):
        ct = _compiled(self.TWO_WORD)
        run = [(4, "the loader reads the boot"), (5, "manifest before the mount")]
        self.assertEqual(clg.scan_run(run, ct), [])

    def test_non_consecutive_lines_are_not_joined(self):
        # Line 10 and line 12 are not neighbours; joining them would invent an
        # adjacency the file does not have and refuse text nobody wrote.
        ct = _compiled(self.TWO_WORD)
        runs = clg.consecutive_runs([(10, "reviewed by the ZQXSEAT"),
                                     (12, "ALPHA lane")])
        self.assertEqual(len(runs), 2, runs)
        self.assertEqual([h for run in runs for h in clg.scan_run(run, ct)], [])

    def test_an_indented_continuation_line_still_matches(self):
        # The wrap's own whitespace is normalized into the single joining
        # space, so an indented continuation reads as one space after the word
        # before it.
        ct = _compiled(self.TWO_WORD)
        run = [(10, "the change was reviewed by the ZQXSEAT"),
               (11, "        ALPHA lane before it landed")]
        self.assertEqual(len(clg.scan_run(run, ct)), 1)

    def test_a_match_may_not_span_a_joining_space_it_did_not_earn(self):
        # The join adds exactly one space between lines. A single-token term
        # broken mid-word by a wrap stays unmatched: the halves are two words.
        ct = _compiled(TOK)
        run = [(1, "see ZQXSE"), (2, "ATA here")]
        self.assertEqual(clg.scan_run(run, ct), [])


class WrappedTermRangeTests(unittest.TestCase):
    """The wrap class end to end, through the range scanner the hook calls."""

    TWO_WORD = "ZQXSEAT" + " " + "ALPHA"

    def _git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, capture_output=True,
                              text=True, check=True, timeout=120)

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.repo = Path(self._td.name)
        self._git("init", "-q")
        self._git("config", "user.email", "t@t")
        self._git("config", "user.name", "t")
        (self.repo / "f.txt").write_text("clean baseline line\n")
        self._git("add", "f.txt")
        self._git("commit", "-qm", "base")

    def tearDown(self):
        self._td.cleanup()

    def test_added_lines_carrying_a_wrapped_term_are_refused(self):
        (self.repo / "f.txt").write_text(
            "clean baseline line\n"
            "the change was reviewed by the ZQXSEAT\n"
            "ALPHA lane before it landed\n")
        self._git("add", "f.txt")
        self._git("commit", "-qm", "docs: a neutral message")
        violations = clg.scan_range("HEAD~1..HEAD", _compiled(self.TWO_WORD), self.repo)
        self.assertEqual(len(violations), 1, violations)
        self.assertEqual(violations[0][0], "f.txt:2",
                         "the location names the line the match starts on")

    def test_a_wrapped_term_in_a_commit_message_is_refused(self):
        (self.repo / "f.txt").write_text("clean baseline line\nanother clean line\n")
        self._git("add", "f.txt")
        self._git("commit", "-qm",
                  "docs: a change\n\nthe wording was settled with the ZQXSEAT\n"
                  "ALPHA lane on Tuesday\n")
        violations = clg.scan_range("HEAD~1..HEAD", _compiled(self.TWO_WORD), self.repo)
        locs = [loc for loc, _ in violations]
        self.assertEqual(len(violations), 1, violations)
        self.assertTrue(locs[0].startswith("commit "), locs)

    def test_a_trailer_breaks_the_run_instead_of_joining_across_it(self):
        # A Co-Authored-By trailer is an authorized home. Dropping it must not
        # make the line before it the neighbour of the line after it.
        (self.repo / "f.txt").write_text("clean baseline line\nanother clean line\n")
        self._git("add", "f.txt")
        self._git("commit", "-qm",
                  "docs: a change\n\nsettled with the ZQXSEAT\n"
                  "Co-Authored-By: someone <x@y.z>\n"
                  "ALPHA lane on Tuesday\n")
        self.assertEqual(
            clg.scan_range("HEAD~1..HEAD", _compiled(self.TWO_WORD), self.repo), [])

    def test_lines_from_two_files_are_not_joined(self):
        (self.repo / "a.txt").write_text("reviewed by the ZQXSEAT\n")
        (self.repo / "b.txt").write_text("ALPHA lane signed off\n")
        self._git("add", "-A")
        self._git("commit", "-qm", "docs: two files")
        self.assertEqual(
            clg.scan_range("HEAD~1..HEAD", _compiled(self.TWO_WORD), self.repo), [])

    def test_a_blank_line_breaks_the_run(self):
        # A paragraph break is not a line wrap. Joining across one would match
        # a phrase the author never wrote.
        (self.repo / "f.txt").write_text(
            "clean baseline line\nreviewed by the ZQXSEAT\n\nALPHA lane signed off\n")
        self._git("add", "f.txt")
        self._git("commit", "-qm", "docs: a neutral message")
        self.assertEqual(
            clg.scan_range("HEAD~1..HEAD", _compiled(self.TWO_WORD), self.repo), [])

    def test_a_clean_wrapped_paragraph_still_passes(self):
        (self.repo / "f.txt").write_text(
            "clean baseline line\nthe loader reads the boot\nmanifest before the mount\n")
        self._git("add", "f.txt")
        self._git("commit", "-qm", "docs: a neutral message")
        self.assertEqual(
            clg.scan_range("HEAD~1..HEAD", _compiled(self.TWO_WORD), self.repo), [])


class RealListWrappedTermTests(unittest.TestCase):
    """Every multi-word entry of the REAL list, wrapped, must be refused.

    The fixtures are built from the private list AT RUN TIME and are never
    written into this tree — the term reaches the scanner as data, the way the
    gate itself reads it. On a machine without the private list the test says so
    and skips, matching the sibling scanner tests: the suite has to stay runnable
    where the private file does not exist, and the gate's own fail-closed
    behaviour when the list is missing is covered separately.
    """

    def setUp(self):
        try:
            path = clg.resolve_list_path(None)
            self.terms = clg.load_terms(path)
        except Exception as exc:                  # ListUnavailable and friends
            raise unittest.SkipTest(
                f"private term list not available on this machine: {exc}")
        self.multi = [t for t in self.terms if re.search(r"[-\s]", t.rstrip("*"))]
        if not self.multi:
            raise unittest.SkipTest("the list carries no multi-token entry")

    def test_every_multi_word_term_is_caught_across_a_wrap(self):
        missed = []
        for term in self.multi:
            bare = term.rstrip("*")
            head, _, tail = bare.partition(" " if " " in bare else "-")
            ct = clg.compile_terms([term])
            run = [(7, f"the note said {head}"), (8, f"{tail} was decided")]
            if not clg.scan_run(run, ct):
                missed.append(len(bare))          # a length, never the term
        self.assertEqual(missed, [],
                         f"{len(missed)} multi-token entries still pass a wrap "
                         f"(lengths only, terms are never printed): {missed}")

    def test_every_multi_word_term_is_still_caught_on_one_line(self):
        missed = []
        for term in self.multi:
            bare = term.rstrip("*")
            ct = clg.compile_terms([term])
            if not clg.scan_line(f"the note said {bare} plainly", ct):
                missed.append(len(bare))
        self.assertEqual(missed, [], f"single-line hits lost: {missed}")
