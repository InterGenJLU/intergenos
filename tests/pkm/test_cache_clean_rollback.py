#!/usr/bin/env python3
"""Tests for pkm.cli._cache_clean_rollback (Q1 rollback substrate GC).

Closes the Decision-A item from docs/audit/T0-5-closure-summary.md:
REPO_ROLLBACK_DIR (/var/cache/pkm/rollback/) grew unbounded under the
Q1 v1.0 rollback orchestration (0d619366). Each `pkm upgrade` deposits
a fresh archive there via `_save_rollback_archive`; nothing pruned the
directory. Operator selected Option 1 (pkm cache clean --rollback
subcommand) on 2026-05-19.

Coverage:
  - Empty / missing rollback dir -> exits 0 with friendly message
  - One archive per installed package -> nothing to clean
  - Multiple archives per installed package -> keep most-recent, remove
    older entries
  - Package no longer installed -> remove all its rollback archives
  - Unparseable filename -> leave untouched + WARN
  - Mixed: installed package with multiples + uninstalled package
  - sys.stdout capture verifies the human-readable summary
"""

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pkm.repo
from pkm.cli import _cache_clean_rollback
from pkm.database import PackageDB


def _touch_archive(rollback_dir, name, version, release=1, age_offset_s=0):
    """Drop a stub archive at rollback_dir + set its mtime relative to now.

    age_offset_s: how many seconds in the past to set mtime. Larger =
    older. Tests use this to make some entries "most recent" vs others.
    """
    fname = f"{name}-{version}-{release}.igos.tar.gz"
    p = rollback_dir / fname
    p.write_bytes(b"stub-archive-payload")
    now = time.time()
    target = now - age_offset_s
    import os as _os
    _os.utime(p, (target, target))
    return p


class CacheCleanRollbackTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.rollback_dir = self.tmp / "rollback"
        self.rollback_dir.mkdir()
        self.db_root = self.tmp / "dbroot"
        self.db_root.mkdir()
        self.db = PackageDB(db_path=str(self.tmp / "pkm.db"), root=str(self.db_root))
        self._patcher = patch.object(pkm.repo, "REPO_ROLLBACK_DIR", self.rollback_dir)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self._tmpdir.cleanup()

    def test_missing_rollback_dir_exits_zero(self):
        # Patch to a path that does not exist.
        ghost = self.tmp / "does-not-exist"
        with patch.object(pkm.repo, "REPO_ROLLBACK_DIR", ghost):
            rc = _cache_clean_rollback(self.db)
        self.assertEqual(rc, 0)

    def test_empty_rollback_dir_exits_zero(self):
        rc = _cache_clean_rollback(self.db)
        self.assertEqual(rc, 0)
        self.assertEqual(list(self.rollback_dir.iterdir()), [])

    def test_single_archive_installed_pkg_kept(self):
        self.db.add_installed("glibc", "2.40", release=1, tier="core")
        kept = _touch_archive(self.rollback_dir, "glibc", "2.40", release=1)
        rc = _cache_clean_rollback(self.db)
        self.assertEqual(rc, 0)
        self.assertTrue(kept.exists())

    def test_multiple_archives_installed_keeps_most_recent(self):
        self.db.add_installed("glibc", "2.40", release=1, tier="core")
        old1 = _touch_archive(self.rollback_dir, "glibc", "2.38", release=1, age_offset_s=200)
        old2 = _touch_archive(self.rollback_dir, "glibc", "2.39", release=1, age_offset_s=100)
        newest = _touch_archive(self.rollback_dir, "glibc", "2.40", release=1, age_offset_s=10)
        rc = _cache_clean_rollback(self.db)
        self.assertEqual(rc, 0)
        self.assertFalse(old1.exists())
        self.assertFalse(old2.exists())
        self.assertTrue(newest.exists())

    def test_package_no_longer_installed_removes_all(self):
        # No db.add_installed -> package is not in installed table.
        a = _touch_archive(self.rollback_dir, "ghost-pkg", "1.0", release=1, age_offset_s=50)
        b = _touch_archive(self.rollback_dir, "ghost-pkg", "1.1", release=1, age_offset_s=10)
        rc = _cache_clean_rollback(self.db)
        self.assertEqual(rc, 0)
        self.assertFalse(a.exists())
        self.assertFalse(b.exists())

    def test_mixed_installed_and_uninstalled(self):
        self.db.add_installed("glibc", "2.40", release=1, tier="core")
        # glibc installed: two old + one newest -> two should be removed.
        g_old = _touch_archive(self.rollback_dir, "glibc", "2.38", release=1, age_offset_s=300)
        g_mid = _touch_archive(self.rollback_dir, "glibc", "2.39", release=1, age_offset_s=200)
        g_new = _touch_archive(self.rollback_dir, "glibc", "2.40", release=1, age_offset_s=10)
        # ghost-pkg uninstalled: both removed.
        ghost_a = _touch_archive(self.rollback_dir, "ghost-pkg", "1.0", release=1, age_offset_s=50)
        ghost_b = _touch_archive(self.rollback_dir, "ghost-pkg", "1.1", release=1, age_offset_s=10)
        rc = _cache_clean_rollback(self.db)
        self.assertEqual(rc, 0)
        self.assertFalse(g_old.exists())
        self.assertFalse(g_mid.exists())
        self.assertTrue(g_new.exists())
        self.assertFalse(ghost_a.exists())
        self.assertFalse(ghost_b.exists())

    def test_unparseable_filename_left_untouched_with_warn(self):
        self.db.add_installed("glibc", "2.40", release=1, tier="core")
        kept = _touch_archive(self.rollback_dir, "glibc", "2.40", release=1, age_offset_s=10)
        # Caught by the glob (.igos.tar.gz suffix matches) but the regex
        # for <name>-<version>-<release> does not match -- triggers the
        # unmatched WARN path.
        weird = self.rollback_dir / "no-version-or-release.igos.tar.gz"
        weird.write_bytes(b"weird")
        import io
        buf = io.StringIO()
        with patch.object(sys, "stderr", buf):
            rc = _cache_clean_rollback(self.db)
        self.assertEqual(rc, 0)
        self.assertTrue(weird.exists())
        self.assertTrue(kept.exists())
        self.assertIn("did not match", buf.getvalue())

    def test_dashed_package_name_parses_correctly(self):
        # linux-firmware has a dash in the name + dashed version.
        # The regex must be non-greedy on name capture so the
        # trailing -<release>.igos.tar.gz anchors correctly.
        self.db.add_installed("linux-firmware", "20251001", release=1, tier="core")
        old = _touch_archive(self.rollback_dir, "linux-firmware", "20240901", release=1, age_offset_s=200)
        new = _touch_archive(self.rollback_dir, "linux-firmware", "20251001", release=1, age_offset_s=10)
        rc = _cache_clean_rollback(self.db)
        self.assertEqual(rc, 0)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())


if __name__ == "__main__":
    unittest.main()
