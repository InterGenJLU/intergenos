#!/usr/bin/env python3
"""Unprivileged dry-run preview (CUT-028 change 2).

The notifier's top-bar click launches `pkm upgrade --all --dry-run` UNPRIVILEGED,
but the root gate keyed on command name alone, so the preview never ran (it
printed the root advisory and exited 1). Now a dry-run invocation is exempt from
the root gate and the mutation lock and opens the DB read-only — strictly
read-only, zero writes — while a NAMED real mutation still refuses under
non-root exactly as before.
"""

import argparse
import hashlib
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import unittest.mock
from unittest.mock import patch

from pkm import cli
from pkm.database import PackageDB


class IsDryRunTest(unittest.TestCase):
    def test_each_dry_run_flag_is_detected(self):
        self.assertTrue(cli._is_dry_run_invocation(
            argparse.Namespace(upgrade_dry_run=True)))
        self.assertTrue(cli._is_dry_run_invocation(
            argparse.Namespace(autoremove_dry_run=True)))
        self.assertTrue(cli._is_dry_run_invocation(
            argparse.Namespace(iso_prep_dry_run=True)))

    def test_real_invocation_is_not_dry_run(self):
        self.assertFalse(cli._is_dry_run_invocation(
            argparse.Namespace(upgrade_dry_run=False)))
        self.assertFalse(cli._is_dry_run_invocation(argparse.Namespace()))


class MutationLockDryRunTest(unittest.TestCase):
    """THE CONTRACT CHANGED, deliberately: a preview takes the SHARED lock.

    It took none at all until the reader lock landed and then for one
    release after it, where the gap was written down rather than closed. A
    preview reads exactly the database a read command reads, so it could be
    handed the same half-written page a reader was — which answered 1 row
    where the truth was 5002 — and the same rewritten page that crashed a
    read with "database disk image is malformed". Reading unprotected is
    what was wrong; the lock is what this asserts now.

    It is the READER'S lock even for a preview of a mutating command,
    because a preview changes nothing: an exclusive lock would shut out
    every other reader for the length of a plan, and would need the lock
    file opened for writing, which the unprivileged account running the
    notifier's preview may not do.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lock_path = Path(self.tmp.name) / "pkm.lock"
        self.lock_path.touch()

    def _observe(self, command, dry_run):
        """What a SECOND process could do while this lock is held.

        The kernel is the instrument, not a mocked constant: with a shared
        lock held, another shared lock succeeds and an exclusive one is
        refused; with an exclusive lock held, both are refused. Returns
        (shared_possible, exclusive_possible).
        """
        import fcntl as real_fcntl
        with patch("pkm.cli.resolve_lock_path", return_value=self.lock_path):
            with cli._pkm_command_lock(command, dry_run=dry_run):
                results = []
                for op in (real_fcntl.LOCK_SH, real_fcntl.LOCK_EX):
                    with open(self.lock_path, "r") as probe:
                        try:
                            real_fcntl.flock(probe.fileno(),
                                             op | real_fcntl.LOCK_NB)
                            results.append(True)
                            real_fcntl.flock(probe.fileno(),
                                             real_fcntl.LOCK_UN)
                        except OSError:
                            results.append(False)
        return tuple(results)

    def test_dry_run_takes_the_shared_lock(self):
        shared_possible, exclusive_possible = self._observe("upgrade",
                                                            dry_run=True)
        self.assertTrue(shared_possible,
                        "another reader was shut out — this is not a shared lock")
        self.assertFalse(exclusive_possible,
                         "a writer could run beside the preview — no lock is held")

    def test_dry_run_of_every_preview_command_takes_the_shared_lock(self):
        for command in ("upgrade", "autoremove", "iso-prep", "vacuum"):
            with self.subTest(command=command):
                shared_possible, exclusive_possible = self._observe(
                    command, dry_run=True)
                self.assertTrue(shared_possible)
                self.assertFalse(exclusive_possible)

    def test_dry_run_opens_the_lock_file_for_reading_only(self):
        """A reader never creates and never truncates the lock file."""
        opened = []
        real_open = open

        def _spy(path, mode="r", *a, **kw):
            if str(path) == str(self.lock_path):
                opened.append(mode)
            return real_open(path, mode, *a, **kw)

        with patch("pkm.cli.resolve_lock_path", return_value=self.lock_path):
            with patch("builtins.open", _spy):
                with cli._pkm_command_lock("upgrade", dry_run=True):
                    pass
        self.assertEqual(opened, ["r"])

    def test_a_real_mutation_still_takes_the_exclusive_lock(self):
        """The preview's route must not soften the real command's lock."""
        shared_possible, exclusive_possible = self._observe("upgrade",
                                                            dry_run=False)
        self.assertFalse(shared_possible)
        self.assertFalse(exclusive_possible)

    def test_a_preview_and_a_real_writer_cannot_overlap(self):
        """The whole point: the page a preview reads is not being rewritten
        under it."""
        import fcntl as real_fcntl
        with open(self.lock_path, "w") as writer:
            real_fcntl.flock(writer.fileno(),
                             real_fcntl.LOCK_EX | real_fcntl.LOCK_NB)
            with patch("pkm.cli.resolve_lock_path",
                       return_value=self.lock_path):
                with self.assertRaises(SystemExit) as exc:
                    with cli._pkm_command_lock("upgrade", dry_run=True,
                                               wait=False):
                        pass
            self.assertEqual(exc.exception.code, 1)
            real_fcntl.flock(writer.fileno(), real_fcntl.LOCK_UN)


class _FakeRepo:
    def __init__(self, remotes):
        self._remotes = remotes

    def has_synced_index(self):
        return True

    def get_package(self, name):
        return self._remotes.get(name)


class RootGateExemptionTest(unittest.TestCase):
    """End-to-end through cli.main(), simulating a non-root caller."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dbpath = Path(self.tmp.name) / "pkm.db"
        db = PackageDB(str(self.dbpath))
        db.add_installed("firefox", "138.0", release=1, tier="desktop")
        db.close()
        self.remotes = {"firefox": {"name": "firefox", "version": "139.0",
                                    "release": 1}}

    def _run(self, extra_argv):
        argv = ["pkm", "--db", str(self.dbpath)] + extra_argv
        out, err = io.StringIO(), io.StringIO()
        rc = 0
        with patch.object(sys, "argv", argv), \
                patch("os.geteuid", return_value=1000), \
                patch("pkm.cli.RepoManager",
                      return_value=_FakeRepo(self.remotes)), \
                redirect_stdout(out), redirect_stderr(err):
            try:
                cli.main()
            except SystemExit as e:
                rc = e.code or 0
        return rc, out.getvalue(), err.getvalue()

    def _db_digest(self):
        return hashlib.sha256(self.dbpath.read_bytes()).hexdigest()

    def test_real_upgrade_refused_under_non_root(self):
        rc, _out, err = self._run(["upgrade", "--all"])
        self.assertEqual(rc, 1)
        self.assertIn("must be run as root", err)

    def test_dry_run_upgrade_runs_under_non_root_and_writes_nothing(self):
        before = self._db_digest()
        rc, out, err = self._run(["upgrade", "--all", "--dry-run"])
        # NOT the root refusal.
        self.assertNotIn("must be run as root", err)
        self.assertEqual(rc, 0)
        # The preview actually ran (plan-only marker or the upgradable listing).
        self.assertTrue("dry-run" in out.lower() or "firefox" in out.lower(),
                        f"preview produced no plan output: {out!r}")
        # Strictly read-only: the DB bytes are unchanged and no WAL sidecar.
        self.assertEqual(self._db_digest(), before, "dry-run must not write the DB")
        self.assertFalse(self.dbpath.with_name(self.dbpath.name + "-wal").exists())

    def test_dry_run_autoremove_not_refused_under_non_root(self):
        _rc, _out, err = self._run(["autoremove", "--dry-run"])
        self.assertNotIn("must be run as root", err)

    def test_dry_run_iso_prep_not_refused_under_non_root(self):
        # iso-prep needs --packages-from; point it at a nonexistent file so the
        # command reaches its own arg handling, NOT the root gate. The assertion
        # is only that the ROOT GATE did not fire.
        _rc, _out, err = self._run(
            ["iso-prep", "--packages-from", "/nonexistent/list", "--dry-run"])
        self.assertNotIn("must be run as root", err)


if __name__ == "__main__":
    unittest.main()
