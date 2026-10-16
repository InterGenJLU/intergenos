# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The install-set audit refuses an archive that is not the build its own
.PKGINFO says it is.

Forge reads an archive name by the version its recipe declares and opens no
.PKGINFO for it, and the build's .PKGINFO sweep only checks that pkgname,
pkgver and pkgrel are present. An earlier build whose upstream version ends
in what reads as a release (version 1.0-7, release-less name demo-1.0-7)
would therefore read as demo 1.0 release 7 against a recipe that now says
1.0, and install as a build it is not. The audit compares every staged
archive's reading with its .PKGINFO and refuses a disagreement, or a
.PKGINFO it cannot read, by name. These cases build real gzip archives and
run the real audit script as a program.
"""

import io
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT = REPO_ROOT / "scripts" / "preflight-install-set.py"


def _archive(path: Path, name, pkginfo=None):
    """A real gzip archive; `pkginfo` (text) becomes its last member, or it
    carries no .PKGINFO at all when None."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tar:
        payload = b"payload\n"
        member = tarfile.TarInfo(f"./usr/bin/{name}")
        member.size = len(payload)
        tar.addfile(member, io.BytesIO(payload))
        if pkginfo is not None:
            body = pkginfo.encode()
            info = tarfile.TarInfo("./.PKGINFO")
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))


def _recipe(packages: Path, tier, name, version=None):
    d = packages / tier / name
    d.mkdir(parents=True, exist_ok=True)
    if version is not None:
        (d / "package.yml").write_text(
            f'name: {name}\nversion: "{version}"\nrelease: 1\ntier: {tier}\n')


class _Audit(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.archives = self.tmp / "archives"
        self.packages = self.tmp / "packages"
        self.archives.mkdir()
        self.packages.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def audit(self):
        return subprocess.run(
            [sys.executable, "-B", str(AUDIT), "--archive-dir",
             str(self.archives), "--packages-dir", str(self.packages),
             "--strict-tier"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=300)

    def assertRefused(self, run, line):
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertIn(line, run.stderr)


class TestTheStaleShapeIsRefusedByName(_Audit):
    def test_a_version_that_reads_as_the_recipes_plus_a_release(self):
        # The recipe says 1.0; the archive is an earlier build whose version
        # was 1.0-7, under the release-less name of that version.
        _recipe(self.packages, "extra", "demo", "1.0")
        _archive(self.archives / "demo-1.0-7.igos.tar.gz", "demo",
                 "pkgname=demo\npkgver=1.0-7\n")
        self.assertRefused(self.audit(),
                           "PKGINFO-DISAGREES: demo-1.0-7.igos.tar.gz reads as "
                           "demo 1.0 release 7 by its recipe's declared version, "
                           "but its .PKGINFO states demo 1.0-7 with no release")

    def test_a_release_the_pkginfo_does_not_state(self):
        _recipe(self.packages, "extra", "demo", "1.0")
        _archive(self.archives / "demo-1.0-7.igos.tar.gz", "demo",
                 "pkgname=demo\npkgver=1.0\npkgrel=6\n")
        self.assertRefused(self.audit(),
                           "PKGINFO-DISAGREES: demo-1.0-7.igos.tar.gz reads as "
                           "demo 1.0 release 7")

    def test_a_name_that_does_not_compose_from_its_own_pkginfo(self):
        # No recipe accounts for the name, so its .PKGINFO is its reading,
        # and that .PKGINFO names another package.
        _recipe(self.packages, "extra", "other")
        _archive(self.archives / "stray-2.0.igos.tar.gz", "stray",
                 "pkgname=other\npkgver=3.1\npkgrel=1\n")
        self.assertRefused(self.audit(),
                           "PKGINFO-DISAGREES: stray-2.0.igos.tar.gz -> its "
                           ".PKGINFO states other 3.1 release 1, which does not "
                           "compose this name")


class TestAPkginfoThatCannotBeReadIsRefusedByName(_Audit):
    def test_an_archive_without_a_pkginfo(self):
        # The recipe reads the name, so nothing else would open the archive.
        _recipe(self.packages, "extra", "demo", "1.0")
        _archive(self.archives / "demo-1.0-7.igos.tar.gz", "demo", None)
        self.assertRefused(self.audit(),
                           "PKGINFO-UNREADABLE: demo-1.0-7.igos.tar.gz -> it "
                           "carries no .PKGINFO stating its package name and "
                           "version")

    def test_a_file_that_is_no_archive(self):
        _recipe(self.packages, "extra", "demo", "1.0")
        (self.archives / "demo-1.0-7.igos.tar.gz").write_text("x")
        self.assertRefused(self.audit(),
                           "PKGINFO-UNREADABLE: demo-1.0-7.igos.tar.gz -> its "
                           ".PKGINFO cannot be read")


class TestArchivesThatAgreePass(_Audit):
    def test_both_name_shapes_agree_and_are_counted(self):
        _recipe(self.packages, "extra", "demo", "1.0")
        _recipe(self.packages, "core", "legacy", "2.0")
        _archive(self.archives / "demo-1.0-7.igos.tar.gz", "demo",
                 "pkgname=demo\npkgver=1.0\npkgrel=7\n")
        # A release-less name states no release, so any pkgrel agrees.
        _archive(self.archives / "legacy-2.0.igos.tar.gz", "legacy",
                 "pkgname=legacy\npkgver=2.0\npkgrel=4\n")
        run = self.audit()
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("pkginfo agreement: 2 of 2 staged archives agree with "
                      "their own .PKGINFO; 0 disagree; 0 unreadable", run.stdout)


if __name__ == "__main__":
    unittest.main()
