# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Pins two push-gate properties that were unpinned and are easy to break silently.

ONE: MERGE COMMIT MESSAGES ARE SCANNED. Measured 2026-08-16 against real pushes — a
banned token in a merge SUBJECT and in a merge BODY are both refused today, because
the two content-scanning message gates enumerate commits with a plain
`git log --format=%H <range>`, which includes merges. Six OTHER pre-push gates use
`rev-list --no-merges`, and the obvious "tidy-up" is to make them all consistent. Doing
that in the wrong direction — adding --no-merges to the scanners — would silently open
the hole, with no test to notice. This is that test.

TWO: MERGE COMMITS STAY EXEMPT FROM THE CONVENTIONAL-COMMIT FORMAT GATE. A git-generated
"Merge branch 'x' into y" subject matches no conventional-commit type, so a format gate
applied to merges would refuse every legitimate merge and train NO-GATE bypass habit.
Decided 2026-08-16: content is the concern, never the git-generated format.

THREE: REF NAMES ARE SCANNABLE. The gate grew a --text mode so pushed ref NAMES could be
checked; nothing else in the suite covers that entry point.

Synthetic tokens only — coined here, carrying no meaning outside this file, because test
fixtures are committed to the public tree and must themselves be clean.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "check-public-language.py"
_HOOK = REPO_ROOT / ".githooks" / "pre-push"
_CONTENT = REPO_ROOT / "scripts" / "check-public-content.py"

_spec = importlib.util.spec_from_file_location("check_public_language_mm", _SCRIPT)
clg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(clg)

TOK = "ZQXMERGEA"        # synthetic token, coined here

# The conventional-commit regex as the hook spells it, kept in lockstep by
# test_format_gate_regex_matches_the_hook below.
COMMIT_TYPE_RE = re.compile(
    r'^(feat|fix|docs|refactor|test|chore|perf|infra|build|ci|revert|phase[0-9]+)'
    r'(\([a-zA-Z0-9_,/.\-]+\))?: .+')


class MergeMessageCoverage(unittest.TestCase):
    """The scanners must keep enumerating merge commits."""

    def test_language_gate_enumerates_merges(self):
        """commit_message_lines must not filter merges out."""
        src = _SCRIPT.read_text(encoding="utf-8")
        fn = src.split("def commit_message_lines", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn(
            "--no-merges", fn,
            "check-public-language.py stopped scanning merge commit messages. A banned "
            "token in a merge subject or body would now reach the public remote "
            "unread. Measured refused 2026-08-16; keep it that way.")

    def test_content_gate_enumerates_merges(self):
        """scan_commit_messages must not filter merges out."""
        src = _CONTENT.read_text(encoding="utf-8")
        fn = src.split("def scan_commit_messages", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn(
            "--no-merges", fn,
            "check-public-content.py stopped scanning merge commit messages. This is "
            "the gate measured refusing a HOME-PATH token in a merge subject and in a "
            "merge body on 2026-08-16.")

    def test_release_note_gate_enumerates_merges(self):
        """A release bump folded into a merge RESOLUTION must stay checkable."""
        src = (REPO_ROOT / "scripts" / "check-release-notes.py").read_text(
            encoding="utf-8")
        fn = src.split("def check_range", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn(
            "--no-merges", fn,
            "check-release-notes.py stopped evaluating merge commits. A release: value "
            "written into a merge conflict resolution exists in neither parent, so no "
            "other commit in the range carries it — measured passing the gate before "
            "this was fixed on 2026-08-16.")


class MergeFormatExemption(unittest.TestCase):
    """The FORMAT gate must keep skipping merges — the other half of the decision."""

    def test_git_generated_merge_subject_is_not_conventional(self):
        self.assertIsNone(
            COMMIT_TYPE_RE.match("Merge branch 'feature/topic' into dev"),
            "If a git-generated merge subject ever matched the conventional-commit "
            "format, this exemption would be moot; it does not, which is exactly why "
            "the format gate must keep using --no-merges.")

    def test_format_gate_still_skips_merges(self):
        hook = _HOOK.read_text(encoding="utf-8")
        block = hook.split("# ---- 5. Conventional-commit format", 1)[1]
        block = block.split("# ---- 6.", 1)[0]
        self.assertIn(
            "--no-merges", block,
            "The conventional-commit FORMAT gate must keep skipping merge commits. "
            "Applying it to merges would refuse every legitimate merge.")

    def test_format_gate_regex_matches_the_hook(self):
        """Guard against this file's copy of the regex drifting from the hook's."""
        hook = _HOOK.read_text(encoding="utf-8")
        m = re.search(r"COMMIT_TYPE_RE='([^']+)'", hook)
        self.assertIsNotNone(m, "could not find COMMIT_TYPE_RE in the pre-push hook")
        self.assertEqual(
            m.group(1), COMMIT_TYPE_RE.pattern.replace("\n", "").replace("    ", ""),
            "the hook's conventional-commit regex changed; update this test's copy")


class RefNameScanMode(unittest.TestCase):
    """The --text entry point the ref-name gate depends on."""

    def test_scan_text_flags_a_bad_ref_name(self):
        hits = clg.scan_text(f"refs/heads/feature/{TOK}-cleanup", "ref", _c(TOK))
        self.assertTrue(hits, "a banned token in a ref name must be reported")

    def test_scan_text_passes_a_clean_ref_name(self):
        self.assertEqual([], clg.scan_text(
            "refs/heads/feature/gate-blind-spots", "ref", _c(TOK)))

    def test_hook_gates_ref_names_and_exempts_deletions(self):
        hook = _HOOK.read_text(encoding="utf-8")
        block = hook.split("# ---- 0a.", 1)[1].split("# ---- 0b.", 1)[0]
        self.assertIn("--label", block, "the ref-name gate must invoke the string mode")
        self.assertIn(
            '"$_ls" = "$ZERO"', block,
            "deletions must stay exempt: deleting a badly-named ref is the only way to "
            "remove one, and gating it would trap the very refs this gate targets.")


def _c(*terms):
    return clg.compile_terms(list(terms))


if __name__ == "__main__":
    unittest.main()
