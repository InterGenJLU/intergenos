# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The squashfs ownership gate knows the archive names the producers write.

scripts/check-squashfs-ownership.py (build-squashfs Step 4.85) lets an archive
ship only when it belongs to an installed package. It decided that by matching
the archive's whole stem against "<name>-<version>" of each installed row. Since
2026-09-22 the producers write "<name>-<version>-<release>.igos.tar.gz", so the
archive of a correctly installed package failed as "not owned by an installed
package" (measured: demo 1.0 release 7 installed, demo-1.0-7.igos.tar.gz, rc 1)
and the next ISO build would have halted at this gate.

The gate now asks pkm/archive_names.py for the names an installed build's
archive may carry: the release-carrying name, and the release-less name every
archive built before that date carries (a lineage build substrate holds those
until each package is rebuilt). A release-less archive lying beside the archive
of the installed release is an earlier build of the same version -- which a
same-version rebuild used to overwrite and now does not -- and is named as a
stale twin.

The gate runs as a program; the package database is written by pkm's own
PackageDB, so the schema is the real one.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "scripts" / "check-squashfs-ownership.py"

sys.path.insert(0, str(REPO_ROOT))
from pkm.database import PackageDB  # noqa: E402

TWIN = "release-less archive beside the archive of the installed release"
NOT_OWNED = "archive not owned by an installed package"


class OwnershipOfReleaseCarryingArchives(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.chroot = root / "chroot"
        self.archives = self.chroot / "var/lib/igos/archives"
        self.archives.mkdir(parents=True)
        (self.chroot / "usr/bin").mkdir(parents=True)
        (self.chroot / "usr/bin/demo").write_text("#!/bin/sh\nexit 0\n")
        (self.chroot / "var/lib/igos/packages").mkdir(parents=True)
        (self.chroot / "var/lib/igos/packages/demo-1.0").write_text(
            "PACKAGE NAME: demo-1.0\n")
        self.allowlist = root / "allowlist.txt"
        self.allowlist.write_text("# no exceptions\n")
        self.excludes = root / "excludes.txt"
        self.excludes.write_text("")
        # The database lives outside the walked tree (--db), so the only
        # files the gate judges are the ones each case places.
        self.db = root / "pkm.db"
        db = PackageDB(str(self.db), root=str(self.chroot))
        pid = db.add_installed("demo", "1.0", release=7, tier="core")
        db.add_files(pid, ["usr/bin/demo"])
        db.close()

    def archive(self, name: str) -> None:
        (self.archives / name).write_bytes(b"")

    def run_gate(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(GATE), "--chroot", str(self.chroot),
             "--db", str(self.db), "--allowlist", str(self.allowlist),
             "--archive-excludes", str(self.excludes)],
            capture_output=True, text=True)

    def test_the_archive_named_for_the_installed_release_is_owned(self):
        self.archive("demo-1.0-7.igos.tar.gz")
        r = self.run_gate()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PASS", r.stdout)

    def test_a_release_less_archive_banked_before_the_change_is_owned(self):
        self.archive("demo-1.0.igos.tar.gz")
        r = self.run_gate()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_release_less_twin_beside_the_installed_releases_archive_is_named(self):
        self.archive("demo-1.0-7.igos.tar.gz")
        self.archive("demo-1.0.igos.tar.gz")
        r = self.run_gate()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(TWIN, r.stdout)
        self.assertIn("/var/lib/igos/archives/demo-1.0.igos.tar.gz", r.stdout)
        self.assertNotIn("demo-1.0-7.igos.tar.gz", r.stdout,
                         "the installed build's own archive is not a finding")

    def test_an_archive_of_another_release_is_not_owned(self):
        self.archive("demo-1.0-7.igos.tar.gz")
        self.archive("demo-1.0-6.igos.tar.gz")
        r = self.run_gate()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(NOT_OWNED, r.stdout)
        self.assertIn("demo-1.0-6.igos.tar.gz", r.stdout)

    def test_an_archive_of_a_package_nobody_installed_is_not_owned(self):
        self.archive("demo-1.0-7.igos.tar.gz")
        self.archive("other-2.0-1.igos.tar.gz")
        r = self.run_gate()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("other-2.0-1.igos.tar.gz", r.stdout)

    def test_mirror_only_archives_on_the_exclusion_list_are_exempt_in_both_shapes(self):
        self.archive("demo-1.0-7.igos.tar.gz")
        self.archive("mirrored-3.0-2.igos.tar.gz")   # rebuilt since the change
        self.archive("banked-4.0.igos.tar.gz")        # banked before it
        self.excludes.write_text(
            "# package: mirrored 3.0 2\n"
            "var/lib/igos/archives/mirrored-3.0-2.igos.tar.gz\n"
            "var/lib/igos/archives/mirrored-3.0.igos.tar.gz\n"
            "# package: banked 4.0 5\n"
            "var/lib/igos/archives/banked-4.0-5.igos.tar.gz\n"
            "var/lib/igos/archives/banked-4.0.igos.tar.gz\n")
        r = self.run_gate()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("archive names on the exclusion list 4", r.stdout)


if __name__ == "__main__":
    unittest.main()
