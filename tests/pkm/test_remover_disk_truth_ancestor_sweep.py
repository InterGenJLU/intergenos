# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Disk-truth path classification + unowned-empty-ancestor sweep in remove.

The empty-skeleton shipping class (decided 2026-07-22): a chroot DB carried
thousands of real directories with is_dir=0, so remove() sent them through
os.remove() — every entry failed into the (discarded) message and the whole
removed package's directory skeleton survived. On the live-ISO prune the
skeletons shipped, and an empty site-packages/<pkg>/ tree read as "installed"
via python namespace-package import. Two remover changes close the class:

  1. Paths are classified by ON-DISK lstat at removal time, never by the DB
     is_dir flag (symlink-to-dir counts as a file: unlink, never descend).
  2. After the recorded paths are processed, the removed manifest's ancestor
     closure is swept deepest-first: rmdir only when empty AND not top-level
     FHS AND not recorded by any still-installed package under EITHER is_dir
     flag (the same bad flag data must not blind the guard).

Acceptance criteria implemented here:
  T1 a directory recorded with is_dir=0 (the bad-flag shape) is still
     cleaned up when emptied, with no failed-removal warning in the message.
  T2 unrecorded intermediate directories (the pip-tree shape) are swept when
     the removal empties them — no skeleton survives.
  T3 an ancestor recorded by ANOTHER installed package survives the sweep
     even when that record carries is_dir=0 (flag-agnostic guard).
  T4 an ancestor still holding another package's file survives (emptiness
     floor), without needing any DB record.
  T5 a recorded symlink-to-directory is unlinked as a file; its target
     directory and contents are untouched.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from pkm.database import PackageDB
from pkm.remover import PackageRemover


class DiskTruthAncestorSweepTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="pkm-disktruth-test-")
        self.root = Path(self._tmp)
        self.db = PackageDB(db_path=str(self.root / "pkm.db"), root=str(self.root))

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _install(self, name, entries, mkdirs=()):
        """entries: relative paths registered in the DB; trailing '/' marks a
        directory ENTRY (is_dir=1). Paths listed in `mkdirs` are created as
        on-disk directories even when their DB entry has no trailing slash —
        the bad-flag shape under test."""
        for rel in entries:
            if rel.endswith("/") or rel.rstrip("/") in mkdirs:
                (self.root / rel.rstrip("/")).mkdir(parents=True, exist_ok=True)
            else:
                p = self.root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                if not p.exists():
                    p.write_bytes(rel.encode())
        pid = self.db.add_installed(name=name, version="1.0",
                                    install_method="archive")
        self.db.add_files(pid, entries)
        return pid

    # ---- T1 ---------------------------------------------------------------
    def test_t1_bad_flag_dir_cleaned_without_failure_warning(self):
        # "usr/share/badflag" registers with is_dir=0 but IS a dir on disk.
        self._install("badflag-pkg",
                      ["usr/share/badflag", "usr/share/badflag/data.txt"],
                      mkdirs=("usr/share/badflag",))
        ok, msg = PackageRemover(self.db, root=str(self.root)).remove("badflag-pkg")
        self.assertTrue(ok, msg)
        self.assertNotIn("could NOT be removed", msg,
                         "a mis-flagged dir must not surface as a failed "
                         "file removal")
        self.assertFalse((self.root / "usr/share/badflag").exists(),
                         "emptied mis-flagged dir must be cleaned up")

    # ---- T2 ---------------------------------------------------------------
    def test_t2_unrecorded_intermediate_dirs_swept(self):
        # Only the leaf files are recorded — the pip-tree shape. The whole
        # chain must vanish once the files are removed.
        self._install("skeleton-pkg", [
            "usr/lib/python3/site-packages/skel/sub/a.py",
            "usr/lib/python3/site-packages/skel/sub/deep/b.py",
        ])
        ok, msg = PackageRemover(self.db, root=str(self.root)).remove("skeleton-pkg")
        self.assertTrue(ok, msg)
        self.assertFalse(
            (self.root / "usr/lib/python3/site-packages/skel").exists(),
            "unrecorded intermediate dirs must not survive as a skeleton")
        # usr itself is top-level FHS and must never be touched
        self.assertTrue((self.root / "usr").is_dir())

    # ---- T3 ---------------------------------------------------------------
    def test_t3_ancestor_recorded_by_other_package_survives(self):
        # keeper records the shared dir WITHOUT a trailing slash (is_dir=0),
        # exercising the flag-agnostic ownership query.
        self._install("keeper", ["usr/share/shared", "usr/bin/keeper"],
                      mkdirs=("usr/share/shared",))
        self._install("goner", ["usr/share/shared/goner.txt"])
        ok, msg = PackageRemover(self.db, root=str(self.root)).remove("goner")
        self.assertTrue(ok, msg)
        self.assertTrue((self.root / "usr/share/shared").is_dir(),
                        "dir recorded by a still-installed package (even "
                        "as is_dir=0) must survive the ancestor sweep")

    # ---- T4 ---------------------------------------------------------------
    def test_t4_nonempty_ancestor_survives(self):
        # No DB record protects the dir — only the emptiness floor does.
        self._install("stays", ["usr/share/mixed/stays.txt"])
        self._install("goes", ["usr/share/mixed/goes.txt"])
        ok, msg = PackageRemover(self.db, root=str(self.root)).remove("goes")
        self.assertTrue(ok, msg)
        self.assertTrue((self.root / "usr/share/mixed/stays.txt").exists(),
                        "another package's file must be untouched")
        self.assertTrue((self.root / "usr/share/mixed").is_dir())

    # ---- T5 ---------------------------------------------------------------
    def test_t5_symlink_to_dir_unlinked_not_descended(self):
        target = self.root / "usr/share/real-target"
        target.mkdir(parents=True)
        (target / "keep.txt").write_bytes(b"keep")
        link_rel = "usr/share/linked"
        os.symlink(str(target), str(self.root / link_rel))
        pid = self.db.add_installed(name="linker", version="1.0",
                                    install_method="archive")
        self.db.add_files(pid, [link_rel])
        ok, msg = PackageRemover(self.db, root=str(self.root)).remove("linker")
        self.assertTrue(ok, msg)
        self.assertFalse(os.path.lexists(self.root / link_rel),
                         "the symlink itself must be removed")
        self.assertTrue((target / "keep.txt").exists(),
                        "the symlink's target contents must be untouched")


if __name__ == "__main__":
    unittest.main()
