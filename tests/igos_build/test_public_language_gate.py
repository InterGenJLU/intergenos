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
