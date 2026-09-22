# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An archive's name and its own header must not disagree about the release.

Since 2026-09-22 a built archive is named
``<name>-<version>-<release>.igos.tar.gz``. The ``.PKGINFO`` sealed inside it
states the release too, and that header is what a machine records when it
installs the package and what the repository index is built from. If the two
disagree, the file says one thing and the system believes another — and
nothing downstream can tell which is right.

``gen-pkginfo.py`` derives the release from the recipe it matches and falls
back to 1 when it matches none, which is the state the ch8 dual-name packages
and the recipe-less core packages reach. The archive step now knows the
release outright, so it passes it and the two can no longer diverge.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "gen-pkginfo.py"


def _fields(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out


class TestTheStampedReleaseCanBeStated(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.files = self.tmp / "staged"
        (self.files / "usr/bin").mkdir(parents=True)
        (self.files / "usr/bin/demo").write_text("#!/bin/sh\nexit 0\n")
        self.repo = self.tmp / "repo"
        (self.repo / "packages").mkdir(parents=True)
        self.out = self.tmp / "PKGINFO"

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, *extra):
        return subprocess.run(
            [sys.executable, str(SCRIPT),
             "--name", "demo", "--version", "1.0",
             "--files-dir", str(self.files),
             "--repo-root", str(self.repo),
             "--fallback-tier", "core",
             "--out", str(self.out), *extra],
            capture_output=True, text=True, timeout=60,
        )

    def test_a_stated_release_is_what_is_stamped(self):
        result = self._run("--release", "7")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        fields = _fields(self.out.read_text())
        self.assertEqual(fields.get("pkgrel"), "7",
                         "the header contradicts the name the archive carries")
        self.assertEqual(fields.get("pkgname"), "demo")
        self.assertEqual(fields.get("pkgver"), "1.0")

    def test_without_one_the_recipe_less_fallback_is_unchanged(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(_fields(self.out.read_text()).get("pkgrel"), "1")

    def test_a_recipes_own_release_still_wins_when_none_is_stated(self):
        recipe_dir = self.repo / "packages" / "core" / "demo"
        recipe_dir.mkdir(parents=True)
        (recipe_dir / "package.yml").write_text(
            "name: demo\nversion: '1.0'\nrelease: 4\n"
            "description: a scratch package\nlicense: GPL-3.0-or-later\n")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(_fields(self.out.read_text()).get("pkgrel"), "4")

    def test_a_release_that_is_not_a_whole_number_is_refused(self):
        result = self._run("--release", "seven")
        self.assertNotEqual(result.returncode, 0,
                            "a release that is not a number was accepted")


if __name__ == "__main__":
    unittest.main()
