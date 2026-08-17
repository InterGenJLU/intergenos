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
    def test_dry_run_takes_no_lock(self):
        # dry_run=True must short-circuit before any fcntl.flock call.
        with patch("pkm.cli.fcntl") as fake_fcntl:
            with cli._pkm_mutation_lock("upgrade", dry_run=True):
                pass
            fake_fcntl.flock.assert_not_called()


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
