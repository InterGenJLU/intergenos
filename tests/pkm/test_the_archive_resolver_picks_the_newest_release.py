# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Resolving a package by name must pick the newest build, not the first read.

The build now names archives ``<name>-<version>-<release>.igos.tar.gz``
(decided 2026-09-22), so an archive directory can hold two releases of one
version at the same time — which is the whole point of the change. The
resolver that turns a bare package name into an archive orders candidates by
pkm's own version comparison; if the release were not ordered, which of two
releases installed would be whatever order the directory happened to be read
in.

These cases run the real resolver against a real archive directory.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pkm.archive_names import archive_filename  # noqa: E402
from pkm.installer import PackageInstaller  # noqa: E402


class TestTheResolverOrdersTheRelease(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.archives = self.root / "var/lib/igos/archives"
        self.archives.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _stage(self, name, version, release=None):
        path = self.archives / archive_filename(name, version, release)
        path.write_bytes(b"archive bytes\n")
        return path

    def _resolve(self, name):
        stub = SimpleNamespace(root=str(self.root))
        return PackageInstaller._find_archive(stub, name)

    def test_the_higher_release_of_one_version_wins(self):
        self._stage("demo", "1.0", 7)
        wanted = self._stage("demo", "1.0", 8)
        self.assertEqual(self._resolve("demo"), wanted)

    def test_release_ten_beats_release_nine(self):
        self._stage("demo", "1.0", 9)
        wanted = self._stage("demo", "1.0", 10)
        self.assertEqual(self._resolve("demo"), wanted)

    def test_a_higher_version_beats_a_higher_release_of_a_lower_one(self):
        self._stage("demo", "1.0", 99)
        wanted = self._stage("demo", "2.0", 1)
        self.assertEqual(self._resolve("demo"), wanted)

    def test_a_package_whose_name_is_a_prefix_of_another_is_not_matched(self):
        self._stage("demo-extras", "5.0", 1)
        wanted = self._stage("demo", "1.0", 1)
        self.assertEqual(self._resolve("demo"), wanted)

    def test_nothing_staged_resolves_to_nothing(self):
        self.assertIsNone(self._resolve("absent"))


if __name__ == "__main__":
    unittest.main()
