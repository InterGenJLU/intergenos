#!/usr/bin/env python3
"""Integration-style tests for pkm.cli.cmd_check_updates (Q8 Phase A).

The notification-surface substrate writes a JSON summary that the systemd
timer + GNOME extension + MOTD line (Phases B/C/D) all consume. These
tests exercise:

  - Empty DB → zero-count JSON written
  - Installed package + no remote → not included in summary
  - Installed + repo same version → not included
  - Installed + repo newer version → included with correct fields
  - --quiet suppresses stdout but JSON still written
  - JSON shape: timestamp / checked_at / count / packages keys present
  - Packages sorted alphabetically by name (stable output for diffing)
  - Output path override honored (tests target temp dir; production uses
    /var/lib/pkm/available-updates.json)
  - Atomic write semantics: tmp file gone after success
  - VersionParseError on one package → WARN-and-skip; others still processed
  - release field threaded through from installed + remote

Pattern: real PackageDB on tempdir + unittest.mock.patch on RepoManager
class so cmd_check_updates' RepoManager() constructor returns a fake
without touching the network or filesystem repos.
"""

import argparse
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from pkm.cli import cmd_check_updates
from pkm.database import PackageDB


def _make_args(quiet=False):
    """Build an argparse.Namespace matching p_check_updates parser output."""
    return argparse.Namespace(quiet=quiet)


def _add_installed(db, name, version, release=1):
    """Insert an installed-package row using the real DB primitive."""
    db.add_installed(name, version, release=release, tier="core")


class _FakeRepoManager:
    """Stand-in for pkm.repo.RepoManager that returns from a fixed dict.

    cmd_check_updates calls repo.get_package(name) per installed package.
    The test injects a mapping {pkg_name: remote_dict} that the fake
    looks up. None → not in any repo (skipped per cmd_check_updates).
    """

    def __init__(self, remotes=None):
        self._remotes = remotes or {}

    def get_package(self, name):
        return self._remotes.get(name)


class TestCheckUpdatesEmptyDB(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "test.db"
        self.out_path = Path(self.tmp.name) / "available-updates.json"
        self.db = PackageDB(str(self.db_path))
        self.addCleanup(self.db.close)

    def test_empty_db_writes_zero_count(self):
        with patch("pkm.cli.RepoManager", return_value=_FakeRepoManager()):
            cmd_check_updates(self.db, _make_args(quiet=True),
                              output_path=self.out_path)
        self.assertTrue(self.out_path.is_file())
        data = json.loads(self.out_path.read_text())
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["packages"], [])
        # JSON shape contract
        self.assertIn("timestamp", data)
        self.assertIn("checked_at", data)
        self.assertIsInstance(data["checked_at"], int)


class TestCheckUpdatesContent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "test.db"
        self.out_path = Path(self.tmp.name) / "available-updates.json"
        self.db = PackageDB(str(self.db_path))
        self.addCleanup(self.db.close)

    def test_no_upgrades_writes_zero(self):
        _add_installed(self.db, "firefox", "139.0")
        remotes = {"firefox": {"name": "firefox", "version": "139.0", "release": 1}}
        with patch("pkm.cli.RepoManager", return_value=_FakeRepoManager(remotes)):
            cmd_check_updates(self.db, _make_args(quiet=True),
                              output_path=self.out_path)
        data = json.loads(self.out_path.read_text())
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["packages"], [])

    def test_installed_with_no_remote_not_included(self):
        _add_installed(self.db, "firefox", "139.0")
        # No remote at all — package skipped per `if not remote: continue`.
        with patch("pkm.cli.RepoManager", return_value=_FakeRepoManager()):
            cmd_check_updates(self.db, _make_args(quiet=True),
                              output_path=self.out_path)
        data = json.loads(self.out_path.read_text())
        self.assertEqual(data["count"], 0)

    def test_upgrades_present_written_with_full_fields(self):
        _add_installed(self.db, "firefox", "138.0", release=1)
        remotes = {"firefox": {"name": "firefox", "version": "139.0", "release": 1}}
        with patch("pkm.cli.RepoManager", return_value=_FakeRepoManager(remotes)):
            cmd_check_updates(self.db, _make_args(quiet=True),
                              output_path=self.out_path)
        data = json.loads(self.out_path.read_text())
        self.assertEqual(data["count"], 1)
        pkg = data["packages"][0]
        self.assertEqual(pkg["name"], "firefox")
        self.assertEqual(pkg["installed_version"], "138.0")
        self.assertEqual(pkg["installed_release"], 1)
        self.assertEqual(pkg["remote_version"], "139.0")
        self.assertEqual(pkg["remote_release"], 1)

    def test_packages_sorted_alphabetically(self):
        _add_installed(self.db, "zsh", "5.9")
        _add_installed(self.db, "bash", "5.2")
        _add_installed(self.db, "fish", "3.7")
        remotes = {
            "zsh": {"name": "zsh", "version": "5.10", "release": 1},
            "bash": {"name": "bash", "version": "5.3", "release": 1},
            "fish": {"name": "fish", "version": "3.8", "release": 1},
        }
        with patch("pkm.cli.RepoManager", return_value=_FakeRepoManager(remotes)):
            cmd_check_updates(self.db, _make_args(quiet=True),
                              output_path=self.out_path)
        data = json.loads(self.out_path.read_text())
        names = [p["name"] for p in data["packages"]]
        self.assertEqual(names, ["bash", "fish", "zsh"])

    def test_release_bump_only_detected(self):
        _add_installed(self.db, "openssl", "3.5.4", release=1)
        remotes = {
            "openssl": {"name": "openssl", "version": "3.5.4", "release": 2}
        }
        with patch("pkm.cli.RepoManager", return_value=_FakeRepoManager(remotes)):
            cmd_check_updates(self.db, _make_args(quiet=True),
                              output_path=self.out_path)
        data = json.loads(self.out_path.read_text())
        # Release bump should count as upgradable per pkm.version.is_upgradable.
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["packages"][0]["remote_release"], 2)


class TestCheckUpdatesAtomicity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "test.db"
        self.out_path = Path(self.tmp.name) / "available-updates.json"
        self.db = PackageDB(str(self.db_path))
        self.addCleanup(self.db.close)

    def test_atomic_write_tmp_file_cleaned_up_on_success(self):
        _add_installed(self.db, "firefox", "138.0")
        remotes = {"firefox": {"name": "firefox", "version": "139.0", "release": 1}}
        with patch("pkm.cli.RepoManager", return_value=_FakeRepoManager(remotes)):
            cmd_check_updates(self.db, _make_args(quiet=True),
                              output_path=self.out_path)
        # Final file exists; tmp sibling does not.
        self.assertTrue(self.out_path.is_file())
        tmp_sibling = self.out_path.with_name(self.out_path.name + ".tmp")
        self.assertFalse(tmp_sibling.exists(),
                         f"tmp file {tmp_sibling} should be gone after successful rename")

    def test_parent_dir_created_if_missing(self):
        # Output path has parent dir that doesn't exist yet — cmd_check_updates
        # should mkdir parents=True before writing.
        nested = Path(self.tmp.name) / "deep" / "nested" / "dir" / "out.json"
        with patch("pkm.cli.RepoManager", return_value=_FakeRepoManager()):
            cmd_check_updates(self.db, _make_args(quiet=True),
                              output_path=nested)
        self.assertTrue(nested.is_file())


class TestCheckUpdatesQuietMode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "test.db"
        self.out_path = Path(self.tmp.name) / "available-updates.json"
        self.db = PackageDB(str(self.db_path))
        self.addCleanup(self.db.close)

    def _capture_stdout(self, fn):
        buf = io.StringIO()
        orig = sys.stdout
        sys.stdout = buf
        try:
            fn()
        finally:
            sys.stdout = orig
        return buf.getvalue()

    def test_quiet_suppresses_stdout(self):
        _add_installed(self.db, "firefox", "138.0")
        remotes = {"firefox": {"name": "firefox", "version": "139.0", "release": 1}}
        with patch("pkm.cli.RepoManager", return_value=_FakeRepoManager(remotes)):
            stdout = self._capture_stdout(
                lambda: cmd_check_updates(self.db, _make_args(quiet=True),
                                          output_path=self.out_path)
            )
        self.assertEqual(stdout, "",
                         f"quiet mode should produce no stdout; got: {stdout!r}")

    def test_non_quiet_prints_summary(self):
        _add_installed(self.db, "firefox", "138.0")
        remotes = {"firefox": {"name": "firefox", "version": "139.0", "release": 1}}
        with patch("pkm.cli.RepoManager", return_value=_FakeRepoManager(remotes)):
            stdout = self._capture_stdout(
                lambda: cmd_check_updates(self.db, _make_args(quiet=False),
                                          output_path=self.out_path)
            )
        self.assertIn("firefox", stdout)
        self.assertIn("138.0", stdout)
        self.assertIn("139.0", stdout)

    def test_non_quiet_zero_updates_says_up_to_date(self):
        with patch("pkm.cli.RepoManager", return_value=_FakeRepoManager()):
            stdout = self._capture_stdout(
                lambda: cmd_check_updates(self.db, _make_args(quiet=False),
                                          output_path=self.out_path)
            )
        self.assertIn("Everything is up to date.", stdout)


class TestCheckUpdatesVersionParseError(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "test.db"
        self.out_path = Path(self.tmp.name) / "available-updates.json"
        self.db = PackageDB(str(self.db_path))
        self.addCleanup(self.db.close)

    def test_bad_version_skipped_others_processed(self):
        # Corrupted version on bad-pkg shouldn't block firefox from being
        # checked. is_upgradable raises VersionParseError on the bad one;
        # cmd_check_updates WARN-and-continues.
        _add_installed(self.db, "bad-pkg", "138.0")
        _add_installed(self.db, "firefox", "138.0")
        remotes = {
            "bad-pkg": {"name": "bad-pkg", "version": "not-a-version", "release": 1},
            "firefox": {"name": "firefox", "version": "139.0", "release": 1},
        }
        with patch("pkm.cli.RepoManager", return_value=_FakeRepoManager(remotes)):
            cmd_check_updates(self.db, _make_args(quiet=True),
                              output_path=self.out_path)
        data = json.loads(self.out_path.read_text())
        names = [p["name"] for p in data["packages"]]
        self.assertNotIn("bad-pkg", names)
        self.assertIn("firefox", names)


if __name__ == "__main__":
    unittest.main()
