#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The helper merge keeps the archive's release when the helper manifest
carries none.

Measured 2026-09-03 on a fresh R001.2 install: `pkm install cuda-toolkit`
registered the archive at release 5 (from its .PKGINFO), then the download
helper's merge rewrote the row with release 1 — the helper manifest carries
no `release_installed`, and the default of 1 overwrote the archive's value.
`pkm list upgradable` then showed a phantom `cuda-toolkit 13.3.1-1 →
13.3.1-5` forever, and a `pkm upgrade` would have re-run the 4 GB helper
every time. Fixed: a manifest without `release_installed` leaves the
existing row's release alone; a manifest that carries one still wins.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from pkm.database import PackageDB
from pkm.installer import PackageInstaller


class HelperMergeKeepsArchiveReleaseTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.tmp / "root"))

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def _run(self, manifest):
        inst = PackageInstaller(self.db, root=str(self.tmp / "root"))
        ok_proc = MagicMock(returncode=0)
        with patch("pkm.installer.subprocess.run", return_value=ok_proc), \
             patch("pkm.installer._read_helper_manifest",
                   return_value=(manifest, None)):
            return inst._run_helper("cuda-toolkit",
                                    self.tmp / "igos-install-cuda-toolkit")

    def test_manifest_without_release_keeps_the_archive_release(self):
        # The archive install registered the package at its .PKGINFO release.
        self.db.add_installed("cuda-toolkit", "13.3.1", release=5,
                              tier="compute", install_method="archive")
        ok, msg, declined = self._run(
            {"version_installed": "13.3.1", "files": [], "symlinks": [],
             "depends": []})
        self.assertTrue(ok, msg)
        row = self.db.get_installed("cuda-toolkit")
        self.assertEqual(row["version"], "13.3.1")
        self.assertEqual(row["release"], 5)
        self.assertEqual(row["install_method"], "helper")

    def test_manifest_with_release_still_wins(self):
        self.db.add_installed("cuda-toolkit", "13.3.1", release=5,
                              tier="compute", install_method="archive")
        ok, msg, declined = self._run(
            {"version_installed": "13.3.1", "release_installed": 7,
             "files": [], "symlinks": [], "depends": []})
        self.assertTrue(ok, msg)
        self.assertEqual(self.db.get_installed("cuda-toolkit")["release"], 7)

    def test_no_existing_row_defaults_to_release_one(self):
        ok, msg, declined = self._run(
            {"version_installed": "13.3.1", "files": [], "symlinks": [],
             "depends": []})
        self.assertTrue(ok, msg)
        self.assertEqual(self.db.get_installed("cuda-toolkit")["release"], 1)


if __name__ == "__main__":
    unittest.main()
