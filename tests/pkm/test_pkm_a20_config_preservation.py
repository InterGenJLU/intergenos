#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""PKM-A20 regression: config preservation is fail-CLOSED + no orphaned baselines.

Two defects:
  1. On removal, an /etc file whose hash raised (unreadable) fell through to
     os.remove — fail-OPEN, silently destroying a possibly user-edited config
     on a transient read error. Fixed: preserve it (fail-CLOSED) + surface it.
  2. remove_installed deleted files/depends/installed but NOT config_files. The
     FK is ON DELETE SET NULL, so the config_files rows orphaned (package_id
     NULL, stale original_checksum). Since path is UNIQUE, a later reinstall's
     baseline INSERT hit ON CONFLICT(path) and KEPT the stale checksum,
     mis-baselining edit-detection across the reinstall. Fixed: delete the
     package's config_files rows on remove so a reinstall re-baselines fresh.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pkm.database import PackageDB
from pkm.remover import PackageRemover


class ConfigPreservationTest(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name) / "root"
        (self.root / "etc").mkdir(parents=True)
        self.db = PackageDB(Path(self._td.name) / "pkm.db", root=str(self.root))

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def _baseline(self, path):
        row = self.db.conn.execute(
            "SELECT original_checksum FROM config_files WHERE path = ?",
            (path,)).fetchone()
        return row[0] if row else None

    def test_config_files_deleted_on_remove_and_reinstall_rebaselines(self):
        conf = self.root / "etc" / "x.conf"

        # First install -> baseline = hash of "v1".
        conf.write_text("v1\n")
        pid1 = self.db.add_installed("pkgx", "1.0", tier="core")
        self.db.add_files(pid1, ["etc/x.conf"])
        base1 = self._baseline("etc/x.conf")
        self.assertIsNotNone(base1)

        # Remove -> the config_files row must be GONE, not orphaned.
        self.db.remove_installed("pkgx")
        count = self.db.conn.execute(
            "SELECT COUNT(*) FROM config_files WHERE path = ?",
            ("etc/x.conf",)).fetchone()[0]
        self.assertEqual(count, 0, "config_files row orphaned after remove (A20)")

        # Reinstall with NEW stock content -> a FRESH baseline, not the stale v1.
        conf.write_text("v2-new-stock\n")
        pid2 = self.db.add_installed("pkgx", "1.1", tier="core")
        self.db.add_files(pid2, ["etc/x.conf"])
        base2 = self._baseline("etc/x.conf")
        self.assertIsNotNone(base2)
        self.assertNotEqual(
            base2, base1,
            "reinstall inherited the stale baseline (orphaned config_files row)")

    def test_unreadable_config_is_preserved_not_deleted(self):
        conf = self.root / "etc" / "y.conf"
        conf.write_text("original\n")
        pid = self.db.add_installed("pkgy", "1.0", tier="core")
        self.db.add_files(pid, ["etc/y.conf"])  # records a real baseline

        remover = PackageRemover(self.db, root=str(self.root))
        # Make the removal-time hash RAISE (file unreadable) — the old code
        # fell through to os.remove (fail-open) and deleted it.
        with patch("pkm.remover._sha256", side_effect=OSError("permission denied")):
            ok, msg = remover.remove("pkgy", force=True)
        self.assertTrue(ok)
        # Fail-CLOSED: the config we could not verify is STILL on disk.
        self.assertTrue(
            conf.exists(), "unreadable config was deleted (fail-open, A20)")
        self.assertIn("could NOT be read", msg)


if __name__ == "__main__":
    unittest.main()
