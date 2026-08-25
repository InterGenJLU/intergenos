# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Tests for the path-scoped exemption in the public-repo language gate.

WHAT IS BEING PROVED. One banned term -- the private repository's directory
name -- is legitimate in exactly one position: the double-quoted shell string a
script under scripts/ uses to find that directory. A script whose job is to
locate a directory has to name it, and the name cannot be reworded without
breaking the search. Everywhere else the term stays banned.

THIS FILE NEVER SPELLS THE TERM. It reads the name out of
scripts/anchor-tracker.sh at run time, which is the one file in the tree
authorized to carry it, so there is exactly one place the literal lives and the
test cannot drift from the script. Every fixture line is built from that value
at run time and none of it is committed.

THE CONTROLS ARE TRUE POSITIVES, not just the exempt case. An exemption that
was never shown to keep blocking the shapes it does not cover is an assertion,
not a measurement, so each of the following is asserted to STILL BLOCK: prose
in a document; the same quoted string in a file outside scripts/; the same
quoted string in a non-shell file under scripts/; a comment and an error
message inside the very shell file the exemption covers; a subject with no
path at all; and a commit message.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "check-public-language.py"

_spec = importlib.util.spec_from_file_location("check_public_language", _SCRIPT)
clg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(clg)

# The one authorized home of the literal in this tree. Read, never duplicated.
_ANCHOR = REPO_ROOT / "scripts" / "anchor-tracker.sh"
_ASSIGN_RE = re.compile(r'^PRIVATE_REPO_DIRNAME="([^"]+)"\s*$', re.MULTILINE)


def _term() -> str:
    m = _ASSIGN_RE.search(_ANCHOR.read_text(encoding="utf-8"))
    if not m:
        raise AssertionError(
            f"{_ANCHOR} no longer states the directory name as a single "
            "double-quoted assignment; this test reads it from there so the "
            "literal lives in exactly one place. Update both together."
        )
    return m.group(1)


# The exempt shape, exactly as the script writes it.
def _assignment_line(term: str) -> str:
    return f'PRIVATE_REPO_DIRNAME="{term}"'


class TermSourceTests(unittest.TestCase):
    def test_the_script_still_states_the_name_once(self):
        term = _term()
        self.assertTrue(term)
        body = _ANCHOR.read_text(encoding="utf-8")
        self.assertEqual(
            len(_ASSIGN_RE.findall(body)), 1,
            "the named constant must be assigned exactly once; a second "
            "assignment is a second literal and the exemption stops being "
            "one line",
        )


class ExemptShapeTests(unittest.TestCase):
    """The one carved position, and nothing beside it."""

    def setUp(self):
        self.term = _term()
        self.ct = clg.compile_terms([self.term])
        self.line = _assignment_line(self.term)

    def test_quoted_string_in_a_shell_script_under_scripts_is_exempt(self):
        self.assertEqual(
            clg.scan_line(self.line, self.ct, "scripts/anchor-tracker.sh"), [],
            "the quoted assignment in a shell script under scripts/ is the "
            "authorized position and must not block",
        )

    def test_any_shell_script_directly_under_scripts_is_covered(self):
        self.assertEqual(
            clg.scan_line(self.line, self.ct, "scripts/some-other-tool.sh"), [])


class TruePositiveControls(unittest.TestCase):
    """Every shape the exemption does NOT cover must still block.

    Each assertion here is the control for the carve above: if the exemption
    were widened by accident, one of these goes green-to-red and names which
    widening happened.
    """

    def setUp(self):
        self.term = _term()
        self.ct = clg.compile_terms([self.term])
        self.line = _assignment_line(self.term)

    def test_prose_in_a_document_still_blocks(self):
        line = f"The rulebooks live in the {self.term} tree."
        self.assertEqual(clg.scan_line(line, self.ct, "docs/operations/11.md"),
                         [self.term])

    def test_prose_in_the_covered_shell_file_still_blocks(self):
        # Same file the exemption covers; a comment is not the carved span.
        line = f"# discovery looks for {self.term} beside the public repo"
        self.assertEqual(
            clg.scan_line(line, self.ct, "scripts/anchor-tracker.sh"),
            [self.term])

    def test_an_error_message_in_the_covered_shell_file_still_blocks(self):
        line = f'    echo "  - $HOME/{self.term}" >&2'
        self.assertEqual(
            clg.scan_line(line, self.ct, "scripts/anchor-tracker.sh"),
            [self.term],
            "the span is the WHOLE double-quoted string equal to the name; a "
            "longer quoted string that merely contains it is not carved",
        )

    def test_the_quoted_string_outside_scripts_still_blocks(self):
        for path in ("tests/preflight/helper.sh", "packages/base/pkm/run.sh",
                     "build/tools/anchor.sh", "anchor-tracker.sh"):
            with self.subTest(path=path):
                self.assertEqual(clg.scan_line(self.line, self.ct, path),
                                 [self.term])

    def test_the_quoted_string_in_a_nested_dir_under_scripts_still_blocks(self):
        # The path pattern is scripts/<one component>.sh — not a subtree.
        self.assertEqual(
            clg.scan_line(self.line, self.ct, "scripts/lib/finder.sh"),
            [self.term])

    def test_the_quoted_string_in_a_non_shell_file_under_scripts_still_blocks(self):
        for path in ("scripts/check-public-language.py", "scripts/notes.md",
                     "scripts/data.json"):
            with self.subTest(path=path):
                self.assertEqual(clg.scan_line(self.line, self.ct, path),
                                 [self.term])

    def test_a_subject_with_no_path_still_blocks(self):
        # A ref name or a bare --text subject carries no path. The default is
        # no exemption, which is the fail-closed direction.
        self.assertEqual(clg.scan_line(self.line, self.ct), [self.term])
        self.assertEqual(
            clg.scan_text(self.line, "ref name", self.ct),
            [("ref name", self.term)])

    def test_a_single_quoted_string_is_not_the_carved_span(self):
        line = f"PRIVATE_REPO_DIRNAME='{self.term}'"
        self.assertEqual(
            clg.scan_line(line, self.ct, "scripts/anchor-tracker.sh"),
            [self.term])

    def test_a_real_hit_beside_the_exempt_span_still_blocks(self):
        # Span-awareness: the carve covers its own span only.
        line = f'{_assignment_line(self.term)}  # mirrors the {self.term} tree'
        self.assertEqual(
            clg.scan_line(line, self.ct, "scripts/anchor-tracker.sh"),
            [self.term])


class RangeScanControls(unittest.TestCase):
    """The same proof through the gate's real entry point, on a real repo."""

    def _git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, check=True,
                       capture_output=True, text=True)

    def setUp(self):
        self.term = _term()
        self.ct = clg.compile_terms([self.term])
        self._td = tempfile.TemporaryDirectory()
        self.repo = Path(self._td.name)
        (self.repo / "scripts").mkdir()
        (self.repo / "docs").mkdir()
        self._git("init", "-q")
        self._git("config", "user.email", "t@t")
        self._git("config", "user.name", "t")
        (self.repo / "scripts" / "tool.sh").write_text("#!/bin/bash\n")
        (self.repo / "docs" / "d.md").write_text("a clean baseline line\n")
        self._git("add", "-A")
        self._git("commit", "-qm", "base")

    def tearDown(self):
        self._td.cleanup()

    def test_added_assignment_in_a_scripts_shell_file_passes(self):
        (self.repo / "scripts" / "tool.sh").write_text(
            "#!/bin/bash\n" + _assignment_line(self.term) + "\n")
        self._git("add", "-A")
        self._git("commit", "-qm", "name the directory the search looks for")
        self.assertEqual(clg.scan_range("HEAD~1..HEAD", self.ct, self.repo), [])

    def test_added_prose_in_a_document_blocks(self):
        (self.repo / "docs" / "d.md").write_text(
            f"a clean baseline line\nthe {self.term} tree holds it\n")
        self._git("add", "-A")
        self._git("commit", "-qm", "a clean neutral message")
        locs = [loc for loc, _ in
                clg.scan_range("HEAD~1..HEAD", self.ct, self.repo)]
        self.assertTrue(any("docs/d.md" in loc for loc in locs),
                        f"document prose missed: {locs}")

    def test_the_same_assignment_in_a_commit_message_blocks(self):
        (self.repo / "scripts" / "tool.sh").write_text(
            "#!/bin/bash\n" + _assignment_line(self.term) + "\n")
        self._git("add", "-A")
        self._git("commit", "-qm", _assignment_line(self.term))
        locs = [loc for loc, _ in
                clg.scan_range("HEAD~1..HEAD", self.ct, self.repo)]
        self.assertTrue(any("commit" in loc for loc in locs),
                        f"commit message missed: {locs}")
        self.assertFalse(any("scripts/tool.sh" in loc for loc in locs),
                         f"the file line should still be exempt: {locs}")


if __name__ == "__main__":
    unittest.main()
