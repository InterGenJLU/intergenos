# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Cross-package directory-ownership guard in `pkm remove`.

The /etc/sysconfig loss (ge9b-03 post-burn audit, decided 2026-07-15):
Component C fixed FILE co-ownership, but the empty-dir cleanup stayed
unguarded — when iso-prep pruned the two mirror-only packages whose files
populated /etc/sysconfig, the second removal found the directory empty and
rmdir'd it out from under intergenos-base-files, whose manifest still
records it. The class is general: any remove can rmdir a shared-but-
momentarily-empty directory on a real installed system.

PackageRemover.remove now refuses the empty-dir rmdir for any directory
another installed package's manifest records (is_dir = 1), unconditionally
(--force scopes to reverse-deps only), and reports the retention.

Acceptance criteria implemented here:
  D1 two packages sharing a dir — removing one leaves the dir on disk even
     though it is empty after the file removals, and reports the retention
     with the owner's name.
  D2 a sole-owned empty dir is still rmdir'd (the guard does not over-retain).
  D3 the /etc/sysconfig replay — two co-owners pruned in sequence while a
     third package records only the directory: the dir survives both removals.
  D4 --force does not override the directory guard.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from pkm.database import PackageDB
from pkm.remover import PackageRemover


class SharedDirGuardTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="pkm-dirguard-test-")
        self.root = Path(self._tmp)
        self.db = PackageDB(db_path=str(self.root / "pkm.db"), root=str(self.root))

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _install(self, name, entries):
        """entries: relative paths; a trailing '/' marks a directory entry."""
        for rel in entries:
            if rel.endswith("/"):
                (self.root / rel).mkdir(parents=True, exist_ok=True)
            else:
                p = self.root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                if not p.exists():
                    p.write_bytes(rel.encode())
        pid = self.db.add_installed(name=name, version="1.0",
                                    install_method="archive")
        self.db.add_files(pid, entries)
        return pid

    # ---- D1 ---------------------------------------------------------------
    def test_d1_shared_dir_survives_remove_and_is_reported(self):
        shared_dir = "etc/sysconfig/"
        self._install("keeper", [shared_dir, "usr/bin/keeper"])
        self._install("goner", [shared_dir, "etc/sysconfig/goner.conf"])

        ok, msg = PackageRemover(self.db, root=str(self.root)).remove("goner")
        self.assertTrue(ok, msg)

        # goner's file is gone; the now-empty shared dir SURVIVES
        self.assertFalse((self.root / "etc/sysconfig/goner.conf").exists())
        self.assertTrue((self.root / "etc/sysconfig").is_dir(),
                        "shared dir must survive the empty-dir cleanup")
        # the retention is reported, naming the still-installed owner
        self.assertIn("keeper", msg)
        self.assertIn("director", msg)  # directory/directories

    # ---- D2 ---------------------------------------------------------------
    def test_d2_sole_owned_empty_dir_still_removed(self):
        self._install("solo", ["usr/share/solo/", "usr/share/solo/data.txt"])
        ok, msg = PackageRemover(self.db, root=str(self.root)).remove("solo")
        self.assertTrue(ok, msg)
        # no co-owner -> the emptied dir is cleaned up as before
        self.assertFalse((self.root / "usr/share/solo").exists(),
                         "sole-owned empty dir must still be rmdir'd")

    # ---- D3 ---------------------------------------------------------------
    def test_d3_iso_prep_replay_dir_survives_sequential_prunes(self):
        # intergenos-base-files records ONLY the directory; the two co-owners
        # own the files inside it. Pruning both co-owners (iso-prep order)
        # empties the dir — it must survive because base-files still records it.
        self._install("base-files", ["etc/sysconfig/"])
        self._install("apache-httpd",
                      ["etc/sysconfig/", "etc/sysconfig/httpd.conf"])
        self._install("memcached",
                      ["etc/sysconfig/", "etc/sysconfig/memcached.conf"])

        rem = PackageRemover(self.db, root=str(self.root))
        for name in ("apache-httpd", "memcached"):
            ok, msg = rem.remove(name)
            self.assertTrue(ok, msg)

        self.assertTrue((self.root / "etc/sysconfig").is_dir(),
                        "dir recorded by a still-installed package must "
                        "survive the co-owner prunes")
        # base-files still verifies clean
        v = self.db.verify_package("base-files")
        self.assertEqual((v["missing"], v["modified"]), ([], []))

    # ---- D5 ---------------------------------------------------------------
    def test_d5_co_owner_with_is_dir_zero_row_still_protects(self):
        # The ge9b-12 replay (2026-07-30): intergenos-base-files records
        # /etc/sysconfig with is_dir=0 — the flag class r26 proved unreliable
        # at scale ("the same bad data must not blind the guard"). The
        # co-owner query must protect the directory under EITHER flag value;
        # an is_dir=1-only query let iso-prep rmdir the shared dir out from
        # under the still-installed owner on two consecutive mint runs.
        (self.root / "etc/sysconfig").mkdir(parents=True)
        pid = self.db.add_installed(name="keeper-badflag", version="1.0",
                                    install_method="archive")
        # No trailing slash -> add_files records is_dir=0 (the live bad row).
        self.db.add_files(pid, ["etc/sysconfig"])
        row = self.db.conn.execute(
            "SELECT is_dir FROM files WHERE path='etc/sysconfig'"
            " AND package_id=?", (pid,)).fetchone()
        self.assertEqual(row[0], 0, "fixture must reproduce the bad-flag row")

        self._install("goner", ["etc/sysconfig/", "etc/sysconfig/goner.conf"])
        ok, msg = PackageRemover(self.db, root=str(self.root)).remove("goner")
        self.assertTrue(ok, msg)
        self.assertFalse((self.root / "etc/sysconfig/goner.conf").exists())
        self.assertTrue((self.root / "etc/sysconfig").is_dir(),
                        "dir recorded is_dir=0 by a still-installed package "
                        "must survive the co-owner's empty-dir cleanup")
        self.assertIn("keeper-badflag", msg)

    # ---- D4 ---------------------------------------------------------------
    def test_d4_force_does_not_override_dir_guard(self):
        shared_dir = "usr/lib/shared-dir/"
        self._install("dir-owner-a", [shared_dir, "usr/bin/a"])
        self._install("dir-owner-b", [shared_dir, "usr/lib/shared-dir/b.so"])
        ok, msg = PackageRemover(self.db, root=str(self.root)).remove(
            "dir-owner-b", force=True)
        self.assertTrue(ok, msg)
        self.assertTrue((self.root / "usr/lib/shared-dir").is_dir(),
                        "--force must not rmdir a co-owned directory")


if __name__ == "__main__":
    unittest.main()
