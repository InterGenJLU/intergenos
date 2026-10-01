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
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch, MagicMock

from pkm import cli
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
                                  verbose=False, json=False,
                                  # `pkm remove` now asks before it removes;
                                  # this case is about the advisory refresh,
                                  # so it states consent rather than testing
                                  # the gate.
                                  remove_yes=True, remove_dry_run=False)
        fake_remover = MagicMock()
        # The whole-set check runs before any removal; this fake's set is
        # removable, so it answers "nothing refuses".
        fake_remover.refusal.return_value = None
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
        # The whole-set check passes and the removal itself then fails, which
        # is the failure that can still reach the loop.
        fake_remover.refusal.return_value = None
        fake_remover.remove.return_value = (False, "not installed")
        with patch("pkm.cli.PackageRemover", return_value=fake_remover), \
                patch("pkm.cli.refresh_available_updates_after_transaction") as ref:
            with self.assertRaises(SystemExit):
                cmd_remove(db, args)
        ref.assert_not_called()


class _UnreadableIndexRepo:
    """The cache read the filesystem refuses: _ensure_synced raises the
    filesystem's own error out of has_synced_index rather than presenting an
    unreadable index as a repository with nothing in it."""

    def has_synced_index(self):
        raise PermissionError(
            13, "Permission denied", "/var/cache/pkm/db/intergenos-current.db")


class _RepoThatRaisesInTheComputation:
    """A synced index whose per-package lookup raises something that is NOT an
    OSError — the shape of a defect inside pkm rather than an I/O fault."""

    def has_synced_index(self):
        return True

    def get_package(self, _name):
        raise RuntimeError("remote table shape changed under us")


class _RepoWhoseIndexFailsInTheComputation:
    """A synced index that goes unreadable between the load and the per-package
    lookups — the computation's only I/O, and still an index read."""

    def has_synced_index(self):
        return True

    def get_package(self, _name):
        raise PermissionError(
            13, "Permission denied", "/var/cache/pkm/db/intergenos-current.db")


class CausesApartTest(unittest.TestCase):
    """The refresh's three causes, each reported in its own words.

    One broad boundary used to report all of them as "could not refresh ... :
    <message>". Measured at the real function under a throwaway root on
    2026-10-01, with a real signed index and a real database: a cached
    index at mode 000, an advisory directory at mode 500 and a closed
    database produced that identical sentence with a bare errno or driver
    message appended, so two I/O faults on two different files — fixed in two
    different places — and a defect inside pkm were indistinguishable. Each
    case below pins one cause's own words AND the absence of the other two,
    because a message that names the wrong cause is worse than a quiet one:
    the person acts on it.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = PackageDB(str(Path(self.tmp.name) / "test.db"))
        self.addCleanup(self.db.close)
        self.advisory_dir = Path(self.tmp.name) / "lib"
        self.advisory_dir.mkdir()
        self.addCleanup(os.chmod, self.advisory_dir, 0o700)
        self.out = self.advisory_dir / "available-updates.json"
        # A previous, good advisory: every failing case must leave it alone.
        self.out.write_text(json.dumps({"count": 7, "packages": ["previous"]}))
        self.previous_bytes = self.out.read_bytes()

    def _install(self, name, version, release=1):
        self.db.add_installed(name, version, release=release, tier="core")

    def _refresh_with(self, repo, out=None):
        """Run the real refresh against `repo` and return what it said, with
        the reporter's line wrapping collapsed so a sentence can be matched."""
        err = io.StringIO()
        with patch("pkm.cli.RepoManager", return_value=repo), \
                redirect_stderr(err):
            # Must never raise: the transaction has already committed.
            refresh_available_updates_after_transaction(
                self.db, self.out if out is None else out)
        return " ".join(err.getvalue().split())

    def _assert_advisory_untouched(self):
        self.assertEqual(self.out.read_bytes(), self.previous_bytes)

    def test_an_unreadable_index_is_named_as_a_read_failure(self):
        self._install("firefox", "138.0")
        said = self._refresh_with(_UnreadableIndexRepo())
        self.assertIn("could not refresh the available-updates advisory", said)
        self.assertIn("the repository index in the local cache could not be "
                      "READ (PermissionError:", said)
        self.assertIn("/var/cache/pkm/db/intergenos-current.db", said)
        self.assertIn("run `pkm sync` to replace the index", said)
        # NOT the other two causes.
        self.assertNotIn("could not be WRITTEN", said)
        self.assertNotIn("INTERNAL ERROR", said)
        self._assert_advisory_untouched()

    def test_an_unwritable_advisory_is_named_as_a_write_failure(self):
        self._install("firefox", "138.0")
        repo = _FakeRepo({"firefox": {"name": "firefox", "version": "139.0",
                                      "release": 1}})
        os.chmod(self.advisory_dir, 0o500)
        said = self._refresh_with(repo)
        self.assertIn("the advisory file could not be WRITTEN "
                      "(PermissionError:", said)
        # The count WAS established; only the file could not be replaced.
        self.assertIn("The new update count was computed from the index "
                      "without trouble", said)
        # NOT the other two causes.
        self.assertNotIn("could not be READ", said)
        self.assertNotIn("INTERNAL ERROR", said)
        self._assert_advisory_untouched()

    def test_an_internal_error_is_named_by_its_type_and_not_as_io(self):
        self._install("firefox", "138.0")
        said = self._refresh_with(_RepoThatRaisesInTheComputation())
        self.assertIn("an INTERNAL ERROR in pkm's update computation "
                      "(RuntimeError: remote table shape changed under us)",
                      said)
        self.assertIn("This is a defect in pkm, not a fault in this system, "
                      "its files or their permissions", said)
        # A programming error never arrives in I/O words.
        self.assertNotIn("could not be READ", said)
        self.assertNotIn("could not be WRITTEN", said)
        self.assertNotIn("permissions, ownership", said)
        self._assert_advisory_untouched()

    def test_the_computations_own_io_is_still_the_index(self):
        # The stage that computes reads one thing: the same cached index, via
        # get_package -> _ensure_synced. An OSError there is a read failure,
        # and saying so is the whole point of attributing by stage.
        self._install("firefox", "138.0")
        said = self._refresh_with(_RepoWhoseIndexFailsInTheComputation())
        self.assertIn("the repository index in the local cache could not be "
                      "READ (PermissionError:", said)
        self.assertNotIn("INTERNAL ERROR", said)
        self._assert_advisory_untouched()

    def test_a_non_oserror_before_the_index_is_still_an_internal_error(self):
        # The first stage's non-OSError (the pre-existing _RaisingRepo case)
        # is the same class of fault and gets the same words, with its type.
        said = self._refresh_with(_RaisingRepo())
        self.assertIn("an INTERNAL ERROR in pkm's update computation "
                      "(RuntimeError: cache read blew up)", said)
        self.assertNotIn("could not be READ", said)
        self._assert_advisory_untouched()

    def test_a_healthy_refresh_still_writes_todays_advisory(self):
        # The no-regression leg: with nothing wrong, the advisory is replaced
        # and says what is upgradable now, in the words it always used.
        self._install("firefox", "138.0")
        repo = _FakeRepo({"firefox": {"name": "firefox", "version": "139.0",
                                      "release": 1}})
        said = self._refresh_with(repo)
        self.assertEqual(said, "")
        data = json.loads(self.out.read_text())
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["packages"][0]["name"], "firefox")
        self.assertEqual(data["skipped_count"], 0)
        self.assertNotEqual(self.out.read_bytes(), self.previous_bytes)


class TransactionExitUnchangedTest(unittest.TestCase):
    """A refresh that cannot write is advisory, not transactional: the command
    that just changed installed state still exits the way it did."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = PackageDB(str(Path(self.tmp.name) / "t.db"))
        self.addCleanup(self.db.close)
        # The real path resolution, rooted in the throwaway tree: the command
        # finds its own advisory path instead of being handed one, so nothing
        # reaches this machine's /var/lib/pkm.
        self.root = Path(self.tmp.name) / "root"
        (self.root / "var" / "lib" / "pkm").mkdir(parents=True)
        cli.set_install_root(str(self.root))
        self.addCleanup(cli.set_install_root, None)
        self.advisory = Path(cli.available_updates_path())
        self.advisory.write_text(json.dumps({"count": 7}))
        self.previous_bytes = self.advisory.read_bytes()
        self.addCleanup(os.chmod, self.advisory.parent, 0o700)

    def _args(self):
        return argparse.Namespace(package="foo", force=False, quiet=True,
                                  verbose=False, json=False,
                                  remove_yes=True, remove_dry_run=False)

    def test_a_removal_that_succeeds_exits_the_same_when_the_write_fails(self):
        fake_remover = MagicMock()
        fake_remover.refusal.return_value = None
        fake_remover.remove.return_value = (True, "removed")
        repo = _FakeRepo({})
        os.chmod(self.advisory.parent, 0o500)
        err = io.StringIO()
        with patch("pkm.cli.PackageRemover", return_value=fake_remover), \
                patch("pkm.cli.RepoManager", return_value=repo), \
                redirect_stderr(err):
            # No SystemExit, no exception: the removal happened and the
            # advisory is a report about it, not part of it.
            answer = cmd_remove(self.db, self._args())
        said = " ".join(err.getvalue().split())
        self.assertIsNone(answer)
        self.assertIn("the advisory file could not be WRITTEN", said)
        self.assertEqual(self.advisory.read_bytes(), self.previous_bytes)


if __name__ == "__main__":
    unittest.main()
