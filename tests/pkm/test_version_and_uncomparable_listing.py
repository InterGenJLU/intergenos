#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Two answers pkm gave that could not be acted on.

`pkm --version` printed the version alone, so an upgrade from release 73 to
release 83 left the same six characters on screen before and after — measured
2026-09-17 on an installed machine, where the package database and the text
manifest both carried the release the whole time and only the command a person
types could not show it.

`pkm list upgradable` answered "Everything is up to date" for an installed
package the repository index held no row for at all. Nothing had been compared;
the sentence said the opposite, and on that same machine it was read as proof a
package's release was correct.
"""

import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pkm.cli as cli
from pkm.database import PackageDB


class TheVersionOptionNamesTheRelease(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.db_path = Path(self._td.name) / "pkm.db"

    def _seed(self, version, release):
        db = PackageDB(self.db_path, root=str(Path(self._td.name) / "root"))
        db.add_installed("pkm", version, release=release, tier="core",
                         install_method="archive")
        db.close()

    def test_it_reads_the_release_from_the_installed_row(self):
        self._seed("0.2.0", 83)
        with patch("pkm.database.DB_PATH", self.db_path):
            self.assertEqual(cli._installed_version_string(), "0.2.0-83")

    def test_two_releases_of_one_version_do_not_read_alike(self):
        self._seed("0.2.0", 73)
        with patch("pkm.database.DB_PATH", self.db_path):
            before = cli._installed_version_string()
        self.db_path.unlink()
        self._seed("0.2.0", 83)
        with patch("pkm.database.DB_PATH", self.db_path):
            after = cli._installed_version_string()
        self.assertNotEqual(before, after)

    def test_an_absent_database_falls_back_to_the_bare_version(self):
        missing = Path(self._td.name) / "not-here.db"
        with patch("pkm.database.DB_PATH", missing):
            self.assertEqual(cli._installed_version_string(), cli.__version__)

    def test_an_unreadable_database_falls_back_rather_than_failing(self):
        self.db_path.write_bytes(b"this is not a database")
        with patch("pkm.database.DB_PATH", self.db_path):
            self.assertEqual(cli._installed_version_string(), cli.__version__)

    def test_a_row_with_no_release_still_answers(self):
        db = PackageDB(self.db_path, root=str(Path(self._td.name) / "root"))
        db.add_installed("pkm", "0.2.0", release=1, tier="core",
                         install_method="archive")
        db.conn.execute("UPDATE installed SET release = NULL WHERE name='pkm'")
        db.conn.commit()
        db.close()
        with patch("pkm.database.DB_PATH", self.db_path):
            self.assertEqual(cli._installed_version_string(), "0.2.0")

    def test_the_option_prints_it_and_exits_zero(self):
        self._seed("0.2.0", 83)
        out = io.StringIO()
        parser = cli.build_parser()
        with patch("pkm.database.DB_PATH", self.db_path):
            with self.assertRaises(SystemExit) as e, redirect_stdout(out):
                parser.parse_args(["--version"])
        self.assertEqual(e.exception.code, 0)
        self.assertIn("0.2.0-83", out.getvalue())


class _Repo:
    """A repository index that carries some packages and not others."""

    def __init__(self, entries):
        self._entries = entries

    def get_package(self, name):
        return self._entries.get(name)


class APackageTheIndexCannotAnswerForIsSaidSo(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name) / "root"
        self.db = PackageDB(Path(self._td.name) / "pkm.db", root=str(self.root))
        self.addCleanup(self.db.close)

    def _list_upgradable(self, repo):
        args = type("A", (), {"what": "upgradable", "tier": None})()
        out = io.StringIO()
        with patch.object(cli, "repo_manager", return_value=repo), \
                redirect_stdout(out):
            cli.cmd_list(self.db, args)
        return out.getvalue()

    def test_a_package_with_no_index_row_is_named_not_passed_over(self):
        self.db.add_installed("cuda-toolkit", "13.3.1", release=7,
                              tier="compute", install_method="helper")
        text = self._list_upgradable(_Repo({}))
        self.assertIn("cuda-toolkit", text)
        self.assertIn("no entry in the repository index", text)
        self.assertNotIn("Everything is up to date.", text)

    def test_everything_checked_and_current_still_says_so_plainly(self):
        self.db.add_installed("acl", "2.3.2", release=1, tier="core",
                              install_method="archive")
        text = self._list_upgradable(
            _Repo({"acl": {"name": "acl", "version": "2.3.2", "release": 1}}))
        self.assertIn("Everything is up to date.", text)
        self.assertNotIn("no entry in the repository index", text)

    def test_a_real_upgrade_is_still_offered_beside_the_uncheckable_ones(self):
        self.db.add_installed("acl", "2.3.1", release=1, tier="core",
                              install_method="archive")
        self.db.add_installed("cuda-toolkit", "13.3.1", release=7,
                              tier="compute", install_method="helper")
        text = self._list_upgradable(
            _Repo({"acl": {"name": "acl", "version": "2.3.2", "release": 1}}))
        self.assertIn("acl", text)
        self.assertIn("2.3.2", text)
        self.assertIn("cuda-toolkit", text)

    def test_a_build_intermediate_is_not_named(self):
        # These are deliberately unpublished; the index is supposed to have no
        # row for them, and `pkm update` already refuses to list them for the
        # same reason. Measured 2026-09-17 on an installed machine: all 19
        # packages with no index row were intermediates, so naming them would
        # have been a wall of noise with nothing a person could act on in it.
        for name in ("glib2-bootstrap", "dbus-pass2", "freetype2-pass1",
                     "something-tmp"):
            self.db.add_installed(name, "1.0", release=1, tier="core",
                                  install_method="archive")
        text = self._list_upgradable(_Repo({}))
        self.assertIn("Everything is up to date.", text)
        self.assertNotIn("no entry in the repository index", text)

    def test_a_real_package_is_still_named_beside_intermediates(self):
        self.db.add_installed("glib2-bootstrap", "1.0", release=1, tier="core",
                              install_method="archive")
        self.db.add_installed("cuda-toolkit", "13.3.1", release=7,
                              tier="compute", install_method="helper")
        text = self._list_upgradable(_Repo({}))
        self.assertIn("cuda-toolkit", text)
        self.assertNotIn("glib2-bootstrap", text)
        self.assertIn("1 installed package has no entry", text)

    def test_a_long_list_is_counted_rather_than_dumped(self):
        for i in range(cli.UNCOMPARABLE_NAMES_SHOWN + 5):
            self.db.add_installed(f"pkg{i:03d}", "1.0", release=1, tier="core",
                                  install_method="archive")
        text = self._list_upgradable(_Repo({}))
        self.assertIn(f"{cli.UNCOMPARABLE_NAMES_SHOWN + 5} installed packages",
                      text)
        self.assertIn("and 5 more", text)
        self.assertNotIn("pkg014", text)

    def test_one_such_package_is_described_in_the_singular(self):
        self.db.add_installed("cuda-toolkit", "13.3.1", release=7,
                              tier="compute", install_method="helper")
        text = self._list_upgradable(_Repo({}))
        self.assertIn("1 installed package has no entry", text)


if __name__ == "__main__":
    unittest.main()
