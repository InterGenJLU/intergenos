#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""PKM-A26 regression: helper path rejects opaque version + traces the merge.

Three issues on the proprietary-download helper install path:
  1. version = manifest.get("version_installed") or "latest" — the installer
     FABRICATED an opaque "latest" sentinel that defeats is_upgradable and lies
     about what is installed. Fixed: drop the fabrication; fail closed if the
     helper reported NO version (it always reports at least "unknown").
  2. The existing-row UPDATE branch bypassed add_installed, the only db-write
     that emits a forensic trace event — so a helper merge was invisible in the
     trace. Fixed: update_helper_merge() emits the db-write event.
  3. release was not threaded on the helper path. Fixed: read release_installed
     (default 1), pass it on both branches.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from pkm.database import PackageDB
from pkm.installer import PackageInstaller


class UpdateHelperMergeTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.db = PackageDB(Path(self._td.name) / "pkm.db",
                            root=str(Path(self._td.name) / "root"))

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def test_update_helper_merge_updates_and_preserves_id(self):
        # Infra row (the igos-install-<app> stub), then the helper merge.
        pkg_id = self.db.add_installed("vscode", "0", tier="extra")
        self.db.update_helper_merge(pkg_id, "vscode", "1.96.2", 3)
        row = self.db.get_installed("vscode")
        self.assertEqual(row["id"], pkg_id)           # id preserved
        self.assertEqual(row["version"], "1.96.2")
        self.assertEqual(row["release"], 3)
        self.assertEqual(row["install_method"], "helper")


class HelperVersionRejectTest(unittest.TestCase):
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
            return inst._run_helper("vscode", self.tmp / "igos-install-vscode")

    def test_empty_version_fails_closed(self):
        ok, msg, declined = self._run(
            {"version_installed": "", "files": [], "symlinks": [], "depends": []})
        self.assertFalse(ok)
        self.assertFalse(declined)
        self.assertIn("version", msg.lower())
        # Not registered.
        self.assertIsNone(self.db.get_installed("vscode"))

    def test_real_version_registers_with_release(self):
        ok, msg, declined = self._run(
            {"version_installed": "1.96.2", "release_installed": 2,
             "files": [], "symlinks": [], "depends": []})
        self.assertTrue(ok, msg)
        row = self.db.get_installed("vscode")
        self.assertIsNotNone(row)
        self.assertEqual(row["version"], "1.96.2")
        self.assertEqual(row["release"], 2)

    def test_unknown_version_is_accepted_not_rejected(self):
        # The honest helper fallback "unknown" is NOT the fabricated "latest"
        # sentinel — it installs (upgrade tracking is best-effort).
        ok, msg, declined = self._run(
            {"version_installed": "unknown", "files": [], "symlinks": [],
             "depends": []})
        self.assertTrue(ok, msg)
        self.assertEqual(self.db.get_installed("vscode")["version"], "unknown")


if __name__ == "__main__":
    unittest.main()
