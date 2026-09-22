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

import io
import sys
import tarfile
import tempfile
import unittest
import contextlib
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



def _real_archive(path, name, version, release, payload=b"payload\n"):
    """A real gzip tar holding one payload file and a sealed header, the header
    last, which is where the project's build puts it."""
    header = f"pkgname = {name}\npkgver = {version}\npkgrel = {release}\n".encode()
    with tarfile.open(path, "w:gz") as tf:
        for member, data in ((f"./usr/share/{name}/payload", payload),
                             ("./.PKGINFO", header)):
            info = tarfile.TarInfo(member)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


class TestTheResolverOrdersTheBuildNotTheString(unittest.TestCase):
    """Measured 2026-09-22 by a second reader: the resolver compared the joined
    ``<version>-<release>`` text with a fixed release of 0, so an older upstream
    version beat a newer one whenever the newer added letters right after the
    older one's last digit. Each case stages REAL archives whose sealed headers
    state the build; the resolver must order by (version, release)."""

    setUp = TestTheResolverOrdersTheRelease.setUp
    tearDown = TestTheResolverOrdersTheRelease.tearDown
    _resolve = TestTheResolverOrdersTheRelease._resolve

    def _stage_real(self, name, version, release, filename_release="same"):
        rel = release if filename_release == "same" else filename_release
        path = self.archives / archive_filename(name, version, rel)
        return _real_archive(path, name, version, release)

    def test_10_0p1_release_1_beats_10_0_release_2(self):
        self._stage_real("demo", "10.0", 2)
        wanted = self._stage_real("demo", "10.0p1", 1)
        self.assertEqual(self._resolve("demo"), wanted)

    def test_1_9_17p2_release_1_beats_1_9_17_release_3(self):
        self._stage_real("demo", "1.9.17", 3)
        wanted = self._stage_real("demo", "1.9.17p2", 1)
        self.assertEqual(self._resolve("demo"), wanted)

    def test_140_9_0esr_release_1_beats_140_9_0_release_4(self):
        self._stage_real("demo", "140.9.0", 4)
        wanted = self._stage_real("demo", "140.9.0esr", 1)
        self.assertEqual(self._resolve("demo"), wanted)

    def test_release_ten_beats_release_nine_read_from_the_headers(self):
        self._stage_real("demo", "1.0", 9)
        wanted = self._stage_real("demo", "1.0", 10)
        self.assertEqual(self._resolve("demo"), wanted)

    def test_a_header_that_names_another_package_is_never_chosen(self):
        wanted = self._stage_real("demo", "1.0", 1)
        planted = self.archives / archive_filename("demo", "9.9", 1)
        _real_archive(planted, "not-demo", "9.9", 1)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = self._resolve("demo")
        self.assertEqual(got, wanted)
        self.assertIn("not-demo", err.getvalue(),
                      "the passed-over archive was not named")

    def test_the_same_build_prefers_the_name_that_states_it(self):
        legacy = self.archives / archive_filename("demo", "1.0", None)
        _real_archive(legacy, "demo", "1.0", 7)
        wanted = self._stage_real("demo", "1.0", 7)
        self.assertEqual(self._resolve("demo"), wanted)

    def test_an_unreadable_newest_candidate_is_returned_and_named(self):
        """Not passed over for an older build: install() refuses it loudly."""
        self._stage_real("demo", "1.0", 1)
        broken = self.archives / archive_filename("demo", "2.0", 1)
        broken.write_bytes(b"not a gzip tar\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = self._resolve("demo")
        self.assertEqual(got, broken)
        self.assertIn(broken.name, err.getvalue())

if __name__ == "__main__":
    unittest.main()
