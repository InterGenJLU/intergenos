# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Tests for scripts/check-remediation-message-quoting.py.

The detector is at proposal stage and is wired into no enforced gate, so these
tests are the only thing that proves it works. They fire the real script as a
subprocess against real git repositories built in a temporary directory: the
defect it detects exists only in the relationship between a commit's diff and
that same commit's message, which no unit test over strings could see.

EVERY TEST USES A SYNTHETIC TERM LIST. Not one real term appears in this file.
That is deliberate and is the same discipline the language gate itself follows:
a test for a wording gate that spells the wording puts it into the tree the gate
exists to keep clean. Synthetic terms also make the tests stronger, because they
prove the mechanism rather than a vocabulary — the detector has no term of its
own to be right about.

The calibration bar: a true-positive control must FIRE, a clean remediation must
PASS, a message naming a term the change did not touch must PASS, and a missing
term list must REFUSE rather than pass. A detector never shown to catch its own
defect cannot certify that a sweep was described honestly.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check-remediation-message-quoting.py"

# Two invented tokens with no meaning anywhere in this tree or outside it.
TERM = "qxwidget-handoff"
OTHER = "qxwidget-relay"

GIT_ID = [
    "-c", "user.name=Test",
    "-c", "user.email=test@example.invalid",
    "-c", "commit.gpgsign=false",
]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *GIT_ID, *args],
                          capture_output=True, text=True)


def _denylist(tmp: Path) -> Path:
    p = tmp / "denylist"
    p.write_text(f"# synthetic list for this test only\n{TERM}\n{OTHER}\n", encoding="utf-8")
    return p


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True)


class ShipsNoTermsOfItsOwnTests(unittest.TestCase):
    def test_the_detector_declares_no_term_list_in_the_public_tree(self):
        # The whole point of reusing the language gate's private list: if this
        # script ever grew its own in-tree list, the terms would live in the
        # repository the gate protects.
        src = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("resolve_list_path", src)
        self.assertNotIn("config/register", src)

    def test_it_refuses_when_the_term_list_cannot_be_read(self):
        r = _run("--message", "anything", "--denylist", "/nonexistent/list")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("REFUSED", r.stderr)

    def test_it_refuses_when_neither_range_nor_message_is_given(self):
        r = _run()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)


class ControlModeTests(unittest.TestCase):
    """--message mode: the caller states what the change removes."""

    def test_true_positive_control_fires(self):
        with tempfile.TemporaryDirectory() as td:
            dl = _denylist(Path(td))
            r = _run("--message", f"docs: drop the {TERM} wording from three recipes",
                     "--removed-term", TERM, "--denylist", str(dl))
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("FAIL", r.stdout)

    def test_the_finding_output_does_not_reprint_the_term(self):
        # The output is read in build logs and pasted into reports; reprinting
        # the wording there is the same defect one level up.
        with tempfile.TemporaryDirectory() as td:
            dl = _denylist(Path(td))
            r = _run("--message", f"docs: drop the {TERM} wording",
                     "--removed-term", TERM, "--denylist", str(dl))
            self.assertEqual(r.returncode, 1)
            self.assertNotIn(TERM, r.stdout)

    def test_a_clean_remediation_message_passes(self):
        with tempfile.TemporaryDirectory() as td:
            dl = _denylist(Path(td))
            r = _run("--message", "docs(recipes): state the disclosure plainly in three comments",
                     "--removed-term", TERM, "--denylist", str(dl))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("PASS", r.stdout)

    def test_naming_a_term_the_change_did_not_remove_is_not_a_finding(self):
        with tempfile.TemporaryDirectory() as td:
            dl = _denylist(Path(td))
            r = _run("--message", f"docs: explain why {OTHER} is disallowed",
                     "--removed-term", TERM, "--denylist", str(dl))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class RangeModeTests(unittest.TestCase):
    """--range mode against a real repository, which is where it will run."""

    def _repo(self, tmp: Path, message: str, before: str, after: str) -> Path:
        repo = tmp / "r"
        repo.mkdir()
        _git(repo, "init", "-q")
        f = repo / "recipe.sh"
        f.write_text(before, encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "base")
        f.write_text(after, encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", message)
        return repo

    def test_a_commit_that_removes_a_term_and_names_it_is_caught(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            repo = self._repo(tmp, f"docs: remove the {TERM} reference from the comment",
                              f"# see the {TERM} for the disclosure\n",
                              "# the disclosure rides with the change that added it\n")
            r = _run("--range", "HEAD~1..HEAD", "--repo", str(repo),
                     "--denylist", str(_denylist(tmp)))
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("FAIL", r.stdout)

    def test_the_same_removal_described_neutrally_passes(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            repo = self._repo(tmp, "docs(recipe): state where the checksum disclosure lives",
                              f"# see the {TERM} for the disclosure\n",
                              "# the disclosure rides with the change that added it\n")
            r = _run("--range", "HEAD~1..HEAD", "--repo", str(repo),
                     "--denylist", str(_denylist(tmp)))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("PASS", r.stdout)

    def test_a_commit_that_ADDS_the_term_and_names_it_is_not_this_defect(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            repo = self._repo(tmp, f"docs: mention the {TERM} in the comment",
                              "# the disclosure rides with the change that added it\n",
                              f"# see the {TERM} for the disclosure\n")
            r = _run("--range", "HEAD~1..HEAD", "--repo", str(repo),
                     "--denylist", str(_denylist(tmp)))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_partial_removal_still_counts_as_a_removal(self):
        # Two occurrences become one: a line-level test would call this "no
        # change", which is why occurrences are counted rather than lines.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            repo = self._repo(tmp, f"docs: thin out the {TERM} references",
                              f"# {TERM} and {TERM} again\n",
                              f"# {TERM} once\n")
            r = _run("--range", "HEAD~1..HEAD", "--repo", str(repo),
                     "--denylist", str(_denylist(tmp)))
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_an_empty_range_passes_without_pretending_to_have_checked(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            repo = self._repo(tmp, "docs: unrelated", "# a\n", "# b\n")
            r = _run("--range", "HEAD..HEAD", "--repo", str(repo),
                     "--denylist", str(_denylist(tmp)))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("nothing in range", r.stdout)


if __name__ == "__main__":
    unittest.main()
