# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A read-only open reads a recorded version that carries its own release the way the repair would write it.

The repair (test_a_version_carrying_its_own_release_is_repaired.py) runs only on
a read-write open. The scheduled update check runs `pkm check-updates`, a
read-only command, in a unit whose file system is read-only, so between an
upgrade by release 73 and the next read-write command the check read the row as
recorded: '1.0-3' compares newer than '1.0', and a newer build of the package
on the mirror was never listed. That is a failure nobody is told about.

The read-only open applies THE SAME exact rule in memory, through the one
function the repair calls: the version ends in "-" plus the row's own release,
and the archive it was installed from states, in its .PKGINFO, the same name,
the same release and the version without that tail. Nothing is written. A row
its archive does not confirm reads exactly as recorded.
"""
from __future__ import annotations

import io
import sqlite3
import tarfile
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from pkm import cli
from pkm import database as pkm_database
from pkm import output
from pkm.database import PackageDB


def _make_archive(path, name, version, release):
    """An archive whose .PKGINFO states name, version and release."""
    pkginfo = (f"pkgname = {name}\npkgver = {version}\npkgrel = {release}\n"
               f"pkgdesc = test package\n").encode()
    with tarfile.open(path, "w:gz") as tf:
        info = tarfile.TarInfo(".PKGINFO")
        info.size = len(pkginfo)
        tf.addfile(info, io.BytesIO(pkginfo))
        body = f"{name}\n".encode()
        info = tarfile.TarInfo(f"usr/share/{name}/marker")
        info.size = len(body)
        tf.addfile(info, io.BytesIO(body))
    return str(path)


class _Index:
    """The local index as _compute_available_updates asks it: one entry per name."""

    def __init__(self, entries):
        self.entries = entries

    def get_package(self, name):
        return self.entries.get(name)


class ReadOnlyOpenReadsTheRule(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pkm-read-only-rule-")
        self.tmp = Path(self._tmp.name)
        self.db_path = self.tmp / "pkm.db"
        self.archives = self.tmp / "archives"
        self.archives.mkdir()
        self._level = output.process_level()

    def tearDown(self):
        output.set_process_level(self._level)
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------

    def _seed(self, rows):
        """Write rows as release 73 left them, after the open (so no repair runs)."""
        db = PackageDB(self.db_path, root=str(self.tmp))
        for name, version, release, archive_path in rows:
            db.add_installed(name=name, version=version, release=release,
                             archive_path=archive_path)
        db.close()

    def _poisoned(self, name="demo", version="1.0", release=3, states=None):
        """A row recorded as '<version>-<release>' and the archive it came from."""
        sv, sr = states if states else (version, release)
        archive = _make_archive(self.archives / f"{name}-{version}-{release}.igos.tar.gz",
                                name, sv, sr)
        self._seed([(name, f"{version}-{release}", release, archive)])

    def _read_only(self):
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            db = PackageDB(self.db_path, root=str(self.tmp), read_only=True)
        return db, err.getvalue()

    def _updates(self, entries):
        db, _ = self._read_only()
        try:
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                packages, skipped = cli._compute_available_updates(db, _Index(entries))
        finally:
            db.close()
        return packages, skipped

    def _recorded(self):
        con = sqlite3.connect(self.db_path)
        try:
            rows = {n: (v, r) for n, v, r in con.execute(
                "SELECT name, version, release FROM installed")}
            history = con.execute(
                "SELECT operation FROM history WHERE operation = 'repair-version'"
            ).fetchall()
        finally:
            con.close()
        return rows, history

    # -- the cases -------------------------------------------------------

    def test_the_scheduled_check_lists_the_newer_build_of_a_poisoned_row(self):
        self._poisoned()
        packages, skipped = self._updates({"demo": {"version": "1.0", "release": 4}})
        self.assertEqual(skipped, [])
        self.assertEqual(
            packages,
            [{"name": "demo", "installed_version": "1.0", "installed_release": 3,
              "remote_version": "1.0", "remote_release": 4}],
            "a read-only check must list the next build of a row release 73 "
            "recorded as '1.0-3'")

    def test_the_same_build_is_not_listed(self):
        self._poisoned()
        packages, _ = self._updates({"demo": {"version": "1.0", "release": 3}})
        self.assertEqual(packages, [])

    def test_info_and_list_read_the_version_the_archive_states(self):
        self._poisoned()
        db, _ = self._read_only()
        try:
            self.assertEqual(db.get_installed("demo")["version"], "1.0")
            self.assertEqual([(p["name"], p["version"], p["release"])
                              for p in db.list_installed()],
                             [("demo", "1.0", 3)])
        finally:
            db.close()

    def test_the_read_only_open_writes_nothing(self):
        self._poisoned()
        before = self.db_path.read_bytes()
        self._updates({"demo": {"version": "1.0", "release": 4}})
        db, _ = self._read_only()
        try:
            db.get_installed("demo")
            db.list_installed()
        finally:
            db.close()
        rows, history = self._recorded()
        self.assertEqual(rows, {"demo": ("1.0-3", 3)})
        self.assertEqual(history, [])
        self.assertEqual(self.db_path.read_bytes(), before)

    def test_a_row_its_archive_does_not_confirm_reads_as_recorded(self):
        # The archive states the dashed version: the upstream form, not a tail.
        self._poisoned(states=("1.0-3", 3))
        packages, _ = self._updates({"demo": {"version": "1.0", "release": 4}})
        self.assertEqual(packages, [])
        db, _ = self._read_only()
        try:
            self.assertEqual(db.get_installed("demo")["version"], "1.0-3")
        finally:
            db.close()

    def test_an_upstream_dash_is_never_read_differently(self):
        archive = _make_archive(self.archives / "dialog-1.3-20260107-2.igos.tar.gz",
                                "dialog", "1.3-20260107", 2)
        self._seed([("dialog", "1.3-20260107", 2, archive)])
        db, _ = self._read_only()
        try:
            self.assertEqual(db.get_installed("dialog")["version"], "1.3-20260107")
            self.assertEqual(db.list_installed()[0]["version"], "1.3-20260107")
        finally:
            db.close()

    def test_pkm_names_its_own_version_through_the_rule(self):
        self._poisoned(name="pkm", version="0.2.0", release=110)
        with mock.patch.object(pkm_database, "DB_PATH", self.db_path):
            self.assertEqual(cli._installed_version_string(), "0.2.0-110")

    def test_the_reading_is_said_under_verbose_and_nowhere_else(self):
        self._poisoned()
        output.set_process_level(output.VERBOSE)
        db, _ = self._read_only()
        try:
            err = io.StringIO()
            with redirect_stderr(err):
                db.get_installed("demo")
        finally:
            db.close()
        self.assertIn("reads demo's recorded version '1.0-3' as '1.0'", err.getvalue())
        output.set_process_level(output.NORMAL)
        db, _ = self._read_only()
        try:
            err = io.StringIO()
            with redirect_stderr(err):
                db.get_installed("demo")
        finally:
            db.close()
        self.assertEqual(err.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
