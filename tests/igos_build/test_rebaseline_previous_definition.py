#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The release tool's re-baseline mode must read the PREVIOUS fingerprint
definition out of the repository, not out of a hardcoded flag.

WHY THIS FILE EXISTS. `--rebaseline` absorbs a change to the fingerprint
DEFINITION: every affected package's digest moves while no shipped byte does,
so the baselines are re-recorded and no release is bumped. It proves what it
absorbs by asking whether the package still fingerprints, under the PREVIOUS
definition, to the value recorded in its recipe. If it does not, the package
really changed and is refused.

The mode computed "the previous definition" as
`content_fingerprint(..., include_siblings=False)` — one hardcoded expression,
written for the one definition change it was built for (the 2026-08-05
sibling-files fold). A hardcoded expression can only ever describe ONE change.
MEASURED 2026-09-18 on the real tree: with `gpu_targets` folded into the
build-affecting recipe keys, `--rebaseline` refused all six trackable ROCm
packages with "content also changed under the previous fingerprint definition",
while `--check`, in the same tree, showed no content change for any of them.
It failed closed, which is right; but its stated reason was false, and a tool
that refuses for a false reason teaches the person in front of it to reach
past it.

The definition is a file this repository tracks, so the previous definition is
a fact git already holds. These tests pin the derivation rules and the two
refusals that keep the mode from becoming a bypass.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "igos-build"))

_spec = importlib.util.spec_from_file_location(
    "bump_changed_releases", REPO_ROOT / "scripts" / "bump-changed-releases.py")
bump = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bump)


def _fake_git(responses):
    """A git stand-in: maps the leading argument words to (rc, stdout)."""
    def run(*args):
        for key, value in responses.items():
            if args[:len(key)] == key:
                return value
        raise AssertionError(f"unexpected git call: {args}")
    return run


class TheDerivationOfThePreviousDefinition(unittest.TestCase):

    def test_a_modified_working_copy_makes_head_the_previous_definition(self):
        """The change being absorbed is the uncommitted one, so the definition
        it replaces is the one HEAD still holds."""
        git = _fake_git({
            ("diff", "--quiet"): (1, ""),
            ("show", "HEAD:igos-build/content_hash.py"): (0, "OLD DEFINITION\n"),
        })
        text, provenance = bump.previous_definition_text(git)
        self.assertEqual(text, "OLD DEFINITION\n")
        self.assertIn("HEAD:igos-build/content_hash.py", provenance)

    def test_a_clean_working_copy_reads_the_commit_before_the_last_change(self):
        """With the definition change committed AS HEAD, the previous
        definition is the copy at HEAD's parent — otherwise the mode would
        compare the new definition against itself and refuse everything."""
        git = _fake_git({
            ("diff", "--quiet"): (0, ""),
            ("log", "-1"): (0, "abc123def4567890\n"),
            ("rev-parse", "HEAD"): (0, "abc123def4567890\n"),
            ("show", "abc123def4567890^:igos-build/content_hash.py"):
                (0, "OLD DEFINITION\n"),
        })
        text, provenance = bump.previous_definition_text(git)
        self.assertEqual(text, "OLD DEFINITION\n")
        self.assertIn("abc123def456^", provenance)

    def test_an_old_definition_change_is_not_reachable_from_a_later_head(self):
        """THE REFUSAL THAT KEEPS THE MODE HONEST. If the derivation kept
        reaching back to the last definition change however old, the mode would
        stay permanently in "absorb that one": a later real change to a field
        the OLD definition ignores fingerprints identically under it, would be
        read as explained by the definition widening, and would be re-baselined
        with the release standing still — a build that reaches no installed
        machine, carrying this tool's own approval."""
        git = _fake_git({
            ("diff", "--quiet"): (0, ""),
            ("log", "-1"): (0, "abc123def4567890\n"),
            ("rev-parse", "HEAD"): (0, "99887766554433221100\n"),
        })
        text, provenance = bump.previous_definition_text(git)
        self.assertIsNone(text)
        self.assertIn("not HEAD", provenance)

    def test_a_definition_with_no_parent_commit_is_refused_with_a_reason(self):
        """Nothing to compare against must come back as a refusal carrying its
        reason, never as an empty previous definition that would make every
        package look unchanged and absorb real drift for free."""
        git = _fake_git({
            ("diff", "--quiet"): (0, ""),
            ("log", "-1"): (0, "abc123def4567890\n"),
            ("rev-parse", "HEAD"): (0, "abc123def4567890\n"),
            ("show", "abc123def4567890^:igos-build/content_hash.py"): (128, ""),
        })
        text, provenance = bump.previous_definition_text(git)
        self.assertIsNone(text)
        self.assertIn("no parent", provenance)

    def test_a_history_that_never_touched_the_file_is_refused(self):
        git = _fake_git({
            ("diff", "--quiet"): (0, ""),
            ("log", "-1"): (0, "\n"),
        })
        text, provenance = bump.previous_definition_text(git)
        self.assertIsNone(text)
        self.assertIn("no commit", provenance)

    def test_a_git_failure_is_refused_rather_than_guessed_at(self):
        git = _fake_git({("diff", "--quiet"): (129, "")})
        text, provenance = bump.previous_definition_text(git)
        self.assertIsNone(text)
        self.assertIn("could not compare", provenance)


class TheLoadedPreviousDefinition(unittest.TestCase):

    def test_it_computes_by_the_rules_of_the_text_it_was_given(self):
        """The point of reading the file back is that the OLD rules run. A
        stand-in definition proves the loaded module's own function answers,
        not the current one."""
        fn = bump.load_fingerprint_fn(
            "def content_fingerprint(pkg, sources_dir):\n"
            "    return 'from-the-previous-definition'\n", "test")
        self.assertEqual(fn(None, None), "from-the-previous-definition")

    def test_a_previous_definition_without_the_function_is_refused(self):
        """An older copy that does not define content_fingerprint must raise
        here, where the caller turns it into a refusal, rather than return
        something callable-looking.

        The message is asserted, not just the exception type: a bare
        assertRaises(AttributeError) also passes when the helper itself is
        missing, which is the state this whole file is supposed to detect.
        Measured while writing it — that one test passed against the base
        script for exactly that wrong reason."""
        with self.assertRaisesRegex(AttributeError, "content_fingerprint"):
            bump.load_fingerprint_fn("X = 1\n", "test")

    def test_the_real_previous_definition_of_this_repository_loads(self):
        """Against the real repository, not a fixture: whatever git reports as
        the previous definition must actually load and expose the function the
        mode calls. A derivation that cannot be executed is not a derivation."""
        text, provenance = bump.previous_definition_text()
        if text is None:
            self.skipTest(f"no previous definition available here: {provenance}")
        fn = bump.load_fingerprint_fn(text, "real")
        self.assertTrue(callable(fn))


if __name__ == "__main__":
    unittest.main()
