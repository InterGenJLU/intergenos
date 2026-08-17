#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""scripts/pkg-functions.sh get_package_release — the bash lane's release read.

The bash builder is what builds the core and base tiers, so its text manifests
are the ones a corpus-wide `pkm import` re-registers from. It reads the release
straight out of package.yml rather than parsing YAML, which is fine only if the
read is anchored and validated: a `release:` string picked up from a comment or
a nested mapping would stamp a confident falsehood into every manifest, and a
stamped falsehood is worse than the silence it replaced.

The fail-safe direction is deliberate and asserted here: anything the helper
cannot read as a plain integer comes back EMPTY, pkg_manifest then omits the
PACKAGE RELEASE header entirely, and pkm's importer leaves the release already
recorded on the row untouched. Empty means "unstated", never "1".
"""
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PKG_FUNCTIONS = REPO_ROOT / "scripts" / "pkg-functions.sh"


class GetPackageReleaseTest(unittest.TestCase):
    def _read(self, yml_text: str) -> str:
        with tempfile.TemporaryDirectory() as td:
            yml = Path(td) / "package.yml"
            yml.write_text(yml_text)
            script = (
                f'source "{PKG_FUNCTIONS}" >/dev/null 2>&1; '
                f'get_package_release "{yml}"'
            )
            r = subprocess.run(["bash", "-c", script],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            return r.stdout

    def test_plain_release(self):
        self.assertEqual(self._read('name: demo\nrelease: 3\n'), "3")

    def test_release_with_trailing_comment(self):
        """Every real recipe annotates the bump inline — that is the norm."""
        text = ('name: linux-kernel\nversion: "6.18.10"\n'
                'release: 8  # r8: boot-menu update + fallback quarantine\n')
        self.assertEqual(self._read(text), "8")

    def test_quoted_release(self):
        self.assertEqual(self._read('release: "12"\n'), "12")

    def test_multi_digit_release(self):
        self.assertEqual(self._read('name: demo\nrelease: 104\n'), "104")

    def test_only_the_first_top_level_release_wins(self):
        """A later stray key must not override the recipe's own release."""
        text = ('name: demo\nrelease: 5\n'
                'description: bumped\nrelease: 99\n')
        self.assertEqual(self._read(text), "5")

    def test_indented_release_is_not_the_package_release(self):
        """A nested mapping's `release:` belongs to that mapping, not the package."""
        text = ('name: demo\nsource:\n  - url: https://example.invalid/x.tar.gz\n'
                '    release: 42\n')
        self.assertEqual(self._read(text), "",
                         "an indented key must not be read as the package release")

    def test_release_inside_a_comment_is_not_read(self):
        text = ('name: demo\n# release: 77 — historical note, not the value\n')
        self.assertEqual(self._read(text), "",
                         "a commented-out line must not be mistaken for the value")

    def test_absent_release_is_empty(self):
        self.assertEqual(self._read('name: demo\nversion: "1.0"\n'), "")

    def test_non_numeric_release_is_empty(self):
        """Unstated beats wrong: the header is omitted and the row keeps its own."""
        self.assertEqual(self._read('release: 3a\n'), "")
        self.assertEqual(self._read('release: latest\n'), "")

    def test_missing_file_is_empty_and_still_exits_clean(self):
        """A missing recipe must not abort the build driver mid-package."""
        script = (f'source "{PKG_FUNCTIONS}" >/dev/null 2>&1; '
                  f'get_package_release "/nonexistent/package.yml"')
        r = subprocess.run(["bash", "-c", script],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")


if __name__ == "__main__":
    unittest.main()
