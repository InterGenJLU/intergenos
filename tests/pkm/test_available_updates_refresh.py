#!/usr/bin/env python3
"""End-of-transaction refresh of the update advisory (CUT-028 change 1).

/var/lib/pkm/available-updates.json was written only by the scheduled check, so
after a transaction changed installed state the notifier's top-bar count stayed
stale until the next timer firing. cmd_upgrade / cmd_install / cmd_remove now
call refresh_available_updates_after_transaction(db), which recomputes
upgradable from the DB against the LOCAL cached index (offline) and rewrites the
JSON atomically. It is best-effort: it degrades loudly-informationally and never
fails the transaction, and it never clobbers a good advisory when no index is
synced.

Pattern (as in test_check_updates): real PackageDB on a tempdir + a fake
RepoManager patched onto pkm.cli.RepoManager.
"""

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch, MagicMock

from pkm.cli import (
    refresh_available_updates_after_transaction,
    _write_available_updates_json,
    cmd_remove,
)
from pkm.database import PackageDB


class _FakeRepo:
    def __init__(self, remotes=None, synced=True):
        self._remotes = remotes or {}
        self._synced = synced

    def has_synced_index(self):
        return self._synced

    def get_package(self, name):
        return self._remotes.get(name)


class _RaisingRepo:
    def has_synced_index(self):
        raise RuntimeError("cache read blew up")


class RefreshTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = PackageDB(str(Path(self.tmp.name) / "test.db"))
        self.addCleanup(self.db.close)
        self.out = Path(self.tmp.name) / "available-updates.json"

    def _install(self, name, version, release=1):
        self.db.add_installed(name, version, release=release, tier="core")

    def test_refresh_writes_current_upgradable(self):
        self._install("firefox", "138.0")
        repo = _FakeRepo({"firefox": {"name": "firefox", "version": "139.0",
                                      "release": 1}})
        with patch("pkm.cli.RepoManager", return_value=repo):
            refresh_available_updates_after_transaction(self.db, self.out)
        data = json.loads(self.out.read_text())
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["packages"][0]["name"], "firefox")
        self.assertEqual(data["packages"][0]["remote_version"], "139.0")

    def test_refresh_reflects_up_to_date_state(self):
        # Post-upgrade truth: the package now matches the remote -> count 0.
        self._install("firefox", "139.0")
        repo = _FakeRepo({"firefox": {"name": "firefox", "version": "139.0",
                                      "release": 1}})
        with patch("pkm.cli.RepoManager", return_value=repo):
            refresh_available_updates_after_transaction(self.db, self.out)
        self.assertEqual(json.loads(self.out.read_text())["count"], 0)

    def test_cache_absent_does_not_clobber(self):
        # A prior good advisory exists...
        self.out.write_text(json.dumps({"count": 3, "packages": [1, 2, 3]}))
        repo = _FakeRepo(synced=False)
        err = io.StringIO()
        with patch("pkm.cli.RepoManager", return_value=repo), redirect_stderr(err):
            refresh_available_updates_after_transaction(self.db, self.out)
        # ...left untouched, and the reason stated loudly.
        self.assertEqual(json.loads(self.out.read_text())["count"], 3)
        # (emit_warn line-wraps; match tokens that survive the wrap)
        self.assertIn("synced", err.getvalue())
        self.assertIn("last scheduled check", err.getvalue())

    def test_unwritable_path_degrades_without_raising(self):
        # Parent is a FILE, so the atomic write's mkdir/open fails -> caught.
        blocker = Path(self.tmp.name) / "blocker"
        blocker.write_text("i am a file")
        target = blocker / "available-updates.json"
        self._install("firefox", "138.0")
        repo = _FakeRepo({"firefox": {"name": "firefox", "version": "139.0",
                                      "release": 1}})
        err = io.StringIO()
        with patch("pkm.cli.RepoManager", return_value=repo), redirect_stderr(err):
            # Must not raise.
            refresh_available_updates_after_transaction(self.db, target)
        self.assertIn("could not refresh", err.getvalue())

    def test_broken_repo_degrades_without_raising(self):
        err = io.StringIO()
        with patch("pkm.cli.RepoManager", return_value=_RaisingRepo()), \
                redirect_stderr(err):
            refresh_available_updates_after_transaction(self.db, self.out)
        self.assertIn("could not refresh", err.getvalue())

    def test_atomic_write_leaves_no_tmp(self):
        summary = {"count": 0, "packages": [], "skipped_count": 0, "skipped": []}
        _write_available_updates_json(summary, self.out)
        self.assertTrue(self.out.is_file())
        self.assertFalse(self.out.with_name(self.out.name + ".tmp").exists())
        # Valid, complete JSON (never a partial).
        self.assertEqual(json.loads(self.out.read_text())["count"], 0)


class RemoveWiringTest(unittest.TestCase):
    def test_cmd_remove_refreshes_advisory_on_success(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = PackageDB(str(Path(tmp.name) / "t.db"))
        self.addCleanup(db.close)
        args = argparse.Namespace(package="foo", force=False, quiet=True,
                                  verbose=False, json=False)
        fake_remover = MagicMock()
        fake_remover.remove.return_value = (True, "removed")
        with patch("pkm.cli.PackageRemover", return_value=fake_remover), \
                patch("pkm.cli.refresh_available_updates_after_transaction") as ref:
            cmd_remove(db, args)
        ref.assert_called_once()

    def test_cmd_remove_does_not_refresh_on_failure(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = PackageDB(str(Path(tmp.name) / "t.db"))
        self.addCleanup(db.close)
        args = argparse.Namespace(package="foo", force=False, quiet=True,
                                  verbose=False, json=False)
        fake_remover = MagicMock()
        fake_remover.remove.return_value = (False, "not installed")
        with patch("pkm.cli.PackageRemover", return_value=fake_remover), \
                patch("pkm.cli.refresh_available_updates_after_transaction") as ref:
            with self.assertRaises(SystemExit):
                cmd_remove(db, args)
        ref.assert_not_called()


if __name__ == "__main__":
    unittest.main()
