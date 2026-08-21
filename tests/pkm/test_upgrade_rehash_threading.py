#!/usr/bin/env python3
"""Upgrade-path install-time re-hash threading.

The install-time re-hash gate in PackageInstaller.install only runs when
the caller threads expected_sha256, and the S5-1 local-resolution
backstop guards only the archive_path=None path — so an install call
that passes an explicit archive_path WITHOUT expected_sha256 runs with
no install-time content check at all. Every download-then-install site
threads the signed-index sha except (historically) the two upgrade-path
sites in cmd_upgrade: the primary post-remove install and the
install-failure rollback re-install.

Coverage:
  - _save_rollback_archive returns (path, sha256) with the sha computed
    from the saved copy; missing cache archive still returns None.
  - cmd_upgrade's primary install call threads the repo index sha256.
  - cmd_upgrade's rollback install call threads the sha recorded at
    rollback-save time.
"""

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pkm.repo
import pkm.cli as cli
from pkm.cli import _save_rollback_archive, cmd_upgrade
from pkm.database import PackageDB


def _upgrade_args(**overrides):
    base = dict(
        packages=["foo"],
        upgrade_all=False,
        allow_downgrade=False,
        ignore_holds=False,
        upgrade_security_only=False,
        upgrade_dry_run=False,
        upgrade_yes=True,
        upgrade_allow_kernel_replace=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class SaveRollbackArchiveShaTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.cache_dir = self.tmp / "cache"
        self.rollback_dir = self.tmp / "rollback"
        self.cache_dir.mkdir()
        self._p1 = patch.object(pkm.repo, "REPO_PKG_CACHE", self.cache_dir)
        self._p2 = patch.object(pkm.repo, "REPO_ROLLBACK_DIR", self.rollback_dir)
        self._p1.start()
        self._p2.start()

    def tearDown(self):
        self._p1.stop()
        self._p2.stop()
        self._tmpdir.cleanup()

    def test_returns_path_and_sha_of_saved_copy(self):
        payload = b"rollback-archive-bytes"
        (self.cache_dir / "foo-1.0-1.igos.tar.gz").write_bytes(payload)
        result = _save_rollback_archive("foo", "1.0", 1)
        self.assertIsNotNone(result)
        dest, sha = result
        self.assertTrue(Path(dest).exists())
        self.assertEqual(sha, hashlib.sha256(payload).hexdigest())

    def test_missing_cache_archive_returns_none(self):
        self.assertIsNone(_save_rollback_archive("foo", "1.0", 1))

    def test_release_less_cached_filename_is_found(self):
        # The live signed index publishes RELEASE-LESS filenames (all
        # 1,126 entries measured 2026-08-21), so this is the shape the
        # pkg cache actually holds. The pre-fix lookup built only the
        # release-qualified name and missed on every real system.
        payload = b"release-less-cache-bytes"
        (self.cache_dir / "foo-1.0.igos.tar.gz").write_bytes(payload)
        result = _save_rollback_archive("foo", "1.0", 1)
        self.assertIsNotNone(result)
        dest, sha = result
        # The saved copy is renamed to the fully-qualified shape the
        # rollback cache's cleaner parses.
        self.assertEqual(Path(dest).name, "foo-1.0-1.igos.tar.gz")
        self.assertEqual(sha, hashlib.sha256(payload).hexdigest())

    def test_release_qualified_shape_preferred_when_both_exist(self):
        qualified = b"qualified-bytes"
        bare = b"bare-bytes"
        (self.cache_dir / "foo-1.0-1.igos.tar.gz").write_bytes(qualified)
        (self.cache_dir / "foo-1.0.igos.tar.gz").write_bytes(bare)
        result = _save_rollback_archive("foo", "1.0", 1)
        self.assertIsNotNone(result)
        _, sha = result
        self.assertEqual(sha, hashlib.sha256(qualified).hexdigest())


class UpgradeRehashThreadingTests(unittest.TestCase):

    REMOTE_SHA = "a" * 64

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.db_root = self.tmp / "root"
        self.db_root.mkdir()
        self.db = PackageDB(
            db_path=str(self.tmp / "pkm.db"), root=str(self.db_root)
        )
        self.db.add_installed("foo", "1.0", release=1, tier="core")

        self.cache_dir = self.tmp / "cache"
        self.rollback_dir = self.tmp / "rollback"
        self.cache_dir.mkdir()
        self.dl_path = self.tmp / "foo-2.0-1.igos.tar.gz"
        self.dl_path.write_bytes(b"new-version-archive")

        self.remote_pkg = {
            "name": "foo",
            "version": "2.0",
            "release": 1,
            "sha256": self.REMOTE_SHA,
            "depends": [],
            "size": 0,
        }
        self.install_calls = []

    def tearDown(self):
        self._tmpdir.cleanup()

    def _run_upgrade(self, install_results):
        """Drive cmd_upgrade with a recording installer.

        install_results: list of (ok, msg) consumed per install call.
        Returns the recorded install calls as (name, kwargs) tuples.
        """
        results = list(install_results)

        def record_install(name, **kwargs):
            self.install_calls.append((name, kwargs))
            return results.pop(0) if results else (True, "ok")

        class FakeRepo:
            def __init__(fr):
                pass

            def get_package(fr, name):
                return self.remote_pkg if name == "foo" else None

            def download_package(fr, name):
                return True, str(self.dl_path)

            def resolve_dependencies(fr, name, db):
                return True, []

        with patch.object(cli, "RepoManager", FakeRepo), \
             patch.object(cli.PackageInstaller, "install",
                          side_effect=record_install, autospec=False), \
             patch.object(pkm.repo, "REPO_PKG_CACHE", self.cache_dir), \
             patch.object(pkm.repo, "REPO_ROLLBACK_DIR", self.rollback_dir), \
             patch("pkm.remover.PackageRemover.remove",
                   return_value=(True, "removed")):
            cmd_upgrade(self.db, _upgrade_args())
        return self.install_calls

    def test_primary_install_threads_repo_index_sha(self):
        calls = self._run_upgrade([(True, "ok")])
        self.assertEqual(len(calls), 1)
        name, kwargs = calls[0]
        self.assertEqual(name, "foo")
        self.assertEqual(kwargs.get("expected_sha256"), self.REMOTE_SHA)

    def test_rollback_install_threads_save_time_sha(self):
        rollback_payload = b"old-version-archive"
        (self.cache_dir / "foo-1.0-1.igos.tar.gz").write_bytes(rollback_payload)
        calls = self._run_upgrade([(False, "install failed"), (True, "ok")])
        self.assertEqual(len(calls), 2)
        rb_name, rb_kwargs = calls[1]
        self.assertEqual(rb_name, "foo")
        self.assertEqual(
            rb_kwargs.get("expected_sha256"),
            hashlib.sha256(rollback_payload).hexdigest(),
        )


if __name__ == "__main__":
    sys.exit(unittest.main())
