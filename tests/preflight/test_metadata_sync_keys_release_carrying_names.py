# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The metadata/payload sync gate files a header-less archive under its package.

scripts/check-iso-metadata-sync.py (build-squashfs Step 2.7) reads every
shipping archive's identity from its own .PKGINFO. Only an archive whose header
cannot be read is filed by its filename, and that filename was taken apart by a
pattern that treated the last hyphen as the version's start. Since 2026-09-22
an archive is named <name>-<version>-<release>.igos.tar.gz, so such an archive
was reported as a package called "<name>-<version>". The gate still failed --
the finding is real -- but under a package name that does not exist. The name
is now read by pkm/archive_names.py.

The gate runs as a program against a scratch image whose database is written
by pkm's own PackageDB.
"""
from __future__ import annotations

import io
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "scripts" / "check-iso-metadata-sync.py"

sys.path.insert(0, str(REPO_ROOT))
from pkm.database import PackageDB  # noqa: E402


class HeaderlessArchivesAreFiledUnderTheirPackage(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.chroot = Path(self._tmp.name) / "chroot"
        self.archives = self.chroot / "var/lib/igos/archives"
        self.archives.mkdir(parents=True)
        (self.chroot / "var/lib/igos/packages").mkdir(parents=True)
        PackageDB(str(self.chroot / "var/lib/igos/pkm.db"),
                  root=str(self.chroot)).close()

    def run_gate(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(GATE), "--chroot", str(self.chroot),
             "--report", str(Path(self._tmp.name) / "report.txt"),
             "--progress-every", "0"],
            capture_output=True, text=True)

    def test_an_unreadable_archive_is_filed_under_its_package_name(self):
        (self.archives / "gamma-2.0-4.igos.tar.gz").write_bytes(b"not a tar")
        r = self.run_gate()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("\n  gamma — 1 finding(s):", r.stdout)
        self.assertIn("unreadable archive gamma-2.0-4.igos.tar.gz", r.stdout)
        self.assertNotIn("gamma-2.0 —", r.stdout)

    def test_an_archive_without_a_header_is_filed_under_its_package_name(self):
        payload = b"payload"
        with tarfile.open(self.archives / "delta-3.1-2.igos.tar.gz", "w:gz") as t:
            info = tarfile.TarInfo("./usr/bin/delta")
            info.size = len(payload)
            t.addfile(info, io.BytesIO(payload))
        r = self.run_gate()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("\n  delta — ", r.stdout)
        self.assertIn("delta-3.1-2.igos.tar.gz: no .PKGINFO", r.stdout)
        self.assertNotIn("delta-3.1 —", r.stdout)


if __name__ == "__main__":
    unittest.main()
