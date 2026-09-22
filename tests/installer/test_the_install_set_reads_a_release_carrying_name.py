# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The install set must not read a release as part of the version.

The build now names archives ``<name>-<version>-<release>.igos.tar.gz``
(decided 2026-09-22). ``get_archives`` parsed the stem with a digit-anchored
regular expression, which reads ``demo-1.0-7`` as version ``1.0-7``; the
version it reports is what the installer records and what it version-compares
against the mirror, so a release read as part of the version is a wrong
version on an installed machine.

The archive carries a ``.PKGINFO`` that states the name, version and release
outright. These cases build real gzip archives with a real ``.PKGINFO`` and
read back what the install set reports.
"""

import io
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from installer.backend import packages as backend  # noqa: E402


def _archive(path: Path, name, version, release, with_pkginfo=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tar:
        if with_pkginfo:
            body = (f"pkgname={name}\npkgver={version}\npkgrel={release}\n"
                    ).encode()
            info = tarfile.TarInfo("./.PKGINFO")
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
        payload = b"payload\n"
        member = tarfile.TarInfo(f"./usr/bin/{name}")
        member.size = len(payload)
        tar.addfile(member, io.BytesIO(payload))


class TestTheInstallSetReadsTheRelease(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.archives = self.tmp / "archives"
        self.archives.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_release_is_not_read_into_the_version(self):
        _archive(self.archives / "demo-1.0-7.igos.tar.gz", "demo", "1.0", 7)
        found = backend.get_archives(self.archives)
        self.assertIn("demo", found,
                      f"the package name was mis-read: {sorted(found)}")
        version, _ = found["demo"]
        self.assertEqual(version, "1.0",
                         "the release was read as part of the version")

    def test_a_hyphenated_upstream_version_still_reads_whole(self):
        _archive(self.archives / "imagemagick-7.1.2-13-4.igos.tar.gz",
                 "imagemagick", "7.1.2-13", 4)
        found = backend.get_archives(self.archives)
        self.assertIn("imagemagick", found, sorted(found))
        self.assertEqual(found["imagemagick"][0], "7.1.2-13")

    def test_a_release_less_archive_still_reads(self):
        _archive(self.archives / "legacy-2.0.igos.tar.gz", "legacy", "2.0", 1)
        found = backend.get_archives(self.archives)
        self.assertIn("legacy", found, sorted(found))
        self.assertEqual(found["legacy"][0], "2.0")

    def test_an_archive_without_a_pkginfo_is_still_read_from_its_name(self):
        # The pre-python bootstrap archives carry no .PKGINFO until the
        # backfill runs. Dropping them would keep a package off every install
        # silently, which is the class this function was already hardened
        # against.
        _archive(self.archives / "early-1.2.igos.tar.gz", "early", "1.2", 1,
                 with_pkginfo=False)
        found = backend.get_archives(self.archives)
        self.assertIn("early", found, sorted(found))
        self.assertEqual(found["early"][0], "1.2")


class TestTwoBuildsOfOnePackageInstallTheNewer(unittest.TestCase):
    """Two archives resolving to one package: the newer BUILD installs.

    Measured 2026-09-22 by a second reader: the collision kept the later name
    in sorted order, so release 8 beat 7 but release 9 beat 10. The kept one is
    now chosen by the (version, release) each sealed header states, and the
    collision is still reported."""

    setUp = TestTheInstallSetReadsTheRelease.setUp
    tearDown = TestTheInstallSetReadsTheRelease.tearDown

    def test_release_ten_installs_over_release_nine(self):
        _archive(self.archives / "demo-1.0-9.igos.tar.gz", "demo", "1.0", 9)
        _archive(self.archives / "demo-1.0-10.igos.tar.gz", "demo", "1.0", 10)
        with self.assertLogs(backend.LOG, level="WARNING") as logs:
            found = backend.get_archives(self.archives)
        self.assertEqual(found["demo"][1].name, "demo-1.0-10.igos.tar.gz")
        self.assertTrue(any("DUPLICATE" in line for line in logs.output), logs.output)

    def test_a_newer_upstream_version_installs_over_a_higher_release(self):
        _archive(self.archives / "demo-10.0-2.igos.tar.gz", "demo", "10.0", 2)
        _archive(self.archives / "demo-10.0p1-1.igos.tar.gz", "demo", "10.0p1", 1)
        with self.assertLogs(backend.LOG, level="WARNING"):
            found = backend.get_archives(self.archives)
        self.assertEqual(found["demo"], ("10.0p1", self.archives / "demo-10.0p1-1.igos.tar.gz"))


if __name__ == "__main__":
    unittest.main()
