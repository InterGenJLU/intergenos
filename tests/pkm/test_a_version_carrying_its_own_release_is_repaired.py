# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A recorded version that carries its own release is repaired when the database opens.

pkm through release 73 took an installed package's version from the archive's
FILE NAME. Since 2026-09-22 a built archive is named
<name>-<version>-<release>.igos.tar.gz, and the index path downloads it under
that name, so release 73 recorded such an archive as version
"<version>-<release>" beside the same release. Measured on an upgraded machine
on 2026-10-15: installed from intergen-0.1.0-335.igos.tar.gz, the row read
version '0.1.0-335', release 335, and the next build of the same version was
refused as a downgrade, "refusing to downgrade intergen 0.1.0-335-335 ->
0.1.0-337". Release 76 onwards takes the version the archive's .PKGINFO states,
so no new row takes that shape; the open repairs the rows release 73 wrote.

THE RULE, exact. A row is repaired only when its version ends in "-" plus its
own release AND the archive it was installed from (archive_path) states, in its
.PKGINFO, the same name, the same release and the version without that tail.
Nothing else counts as evidence. A version whose upstream form holds a dash
(dialog 1.3-20260107, imagemagick 7.1.2-13, dated themes) does not end in its
own release, or its archive states the dashed form, and is never touched. A
row whose archive is gone, unreadable or states anything else is left as it is.
"""
from __future__ import annotations

import io
import os
import sqlite3
import tarfile
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from pkm import output
from pkm.database import PackageDB
from pkm.txn import downgrade_decision


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


# The eleven rows of an upgraded machine whose upstream version holds a dash,
# as its database records them (2026-10-15): name, version, release.
UPSTREAM_DASHES = [
    ("dialog", "1.3-20260107", 2),
    ("fluent-icon-theme", "2025-08-21", 1),
    ("imagemagick", "7.1.2-13", 1),
    ("tela-icon-theme", "2025-02-10", 1),
    ("whitesur-icon-theme", "2025-12-27", 1),
    ("fluent-gtk-theme", "2025-04-17", 1),
    ("graphite-gtk-theme", "2025-07-06", 1),
    ("orchis-theme", "2025-04-25", 1),
    ("whitesur-gtk-theme", "2025-07-24", 1),
    ("chrome", "153.0.8010.52-1", 3),
    ("vscode", "1.141.1-1791833136", 4),
]


class RepairAtTheOpen(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pkm-version-repair-")
        self.tmp = Path(self._tmp.name)
        self.db_path = self.tmp / "pkm.db"
        self.archives = self.tmp / "archives"
        self.archives.mkdir()
        self._level = output.process_level()

    def tearDown(self):
        output.set_process_level(self._level)
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------

    def _archive(self, file_name, name, version, release):
        return _make_archive(self.archives / file_name, name, version, release)

    def _seed(self, rows):
        """Write rows as the shipped release-73 install left them."""
        db = PackageDB(self.db_path, root=str(self.tmp))
        for name, version, release, archive_path in rows:
            db.add_installed(name=name, version=version, release=release,
                             archive_path=archive_path)
        db.close()

    def _open(self, read_only=False):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            db = PackageDB(self.db_path, root=str(self.tmp), read_only=read_only)
        return db, out.getvalue(), err.getvalue()

    def _rows(self):
        con = sqlite3.connect(self.db_path)
        try:
            return {n: (v, r) for n, v, r in con.execute(
                "SELECT name, version, release FROM installed")}
        finally:
            con.close()

    def _history(self):
        con = sqlite3.connect(self.db_path)
        try:
            return con.execute(
                "SELECT operation, package_name, old_version, new_version, method "
                "FROM history ORDER BY id").fetchall()
        finally:
            con.close()

    # -- the cases -------------------------------------------------------

    def test_a_row_written_from_a_release_carrying_name_is_repaired(self):
        path = self._archive("intergen-0.1.0-335.igos.tar.gz",
                             "intergen", "0.1.0", 335)
        self._seed([("intergen", "0.1.0-335", 335, path)])

        db, out, err = self._open()
        db.close()

        self.assertEqual(self._rows()["intergen"], ("0.1.0", 335))
        lines = [ln for ln in err.splitlines() if "intergen" in ln]
        self.assertEqual(len(lines), 1, err)
        self.assertIn("'0.1.0-335'", lines[0])
        self.assertIn("'0.1.0'", lines[0])
        self.assertIn(path, lines[0])
        self.assertEqual(out, "")
        self.assertEqual(
            self._history(),
            [("repair-version", "intergen", "0.1.0-335", "0.1.0",
              "archive-pkginfo")])

    def test_the_line_is_printed_at_every_verbosity(self):
        path = self._archive("widget-1.0-3.igos.tar.gz", "widget", "1.0", 3)
        self._seed([("widget", "1.0-3", 3, path)])
        output.set_process_level(output.QUIET)

        db, out, err = self._open()
        db.close()

        self.assertEqual(self._rows()["widget"], ("1.0", 3))
        self.assertEqual(len([ln for ln in err.splitlines() if "widget" in ln]), 1, err)

    def test_a_second_open_changes_nothing_and_says_nothing(self):
        path = self._archive("widget-1.0-3.igos.tar.gz", "widget", "1.0", 3)
        self._seed([("widget", "1.0-3", 3, path)])
        db, _, _ = self._open()
        db.close()
        before = self.db_path.read_bytes()

        db, out, err = self._open()
        db.close()

        self.assertEqual(self._rows()["widget"], ("1.0", 3))
        self.assertEqual((out, err), ("", ""))
        self.assertEqual(len(self._history()), 1)
        self.assertEqual(self.db_path.read_bytes(), before)

    def test_upstream_versions_holding_a_dash_are_never_touched(self):
        rows = []
        for name, version, release in UPSTREAM_DASHES:
            path = self._archive(f"{name}-{version}.igos.tar.gz", name, version, release)
            rows.append((name, version, release, path))
        # A dashed upstream version that happens to end in its own release:
        # its archive states the dashed form, so the tail is the version's own.
        path = self._archive("gadget-2.0-1-1.igos.tar.gz", "gadget", "2.0-1", 1)
        rows.append(("gadget", "2.0-1", 1, path))
        self._seed(rows)
        before = self._rows()

        db, out, err = self._open()
        db.close()

        self.assertEqual(self._rows(), before)
        self.assertEqual(len(before), len(UPSTREAM_DASHES) + 1)
        self.assertEqual(err, "")
        self.assertEqual(self._history(), [])

    def test_a_row_whose_archive_does_not_confirm_it_is_left_as_it_is(self):
        other_release = self._archive("w2-1.0-3.igos.tar.gz", "w2", "1.0", 4)
        other_name = self._archive("w3-1.0-3.igos.tar.gz", "not-w3", "1.0", 3)
        other_version = self._archive("w4-1.0-3.igos.tar.gz", "w4", "1.1", 3)
        no_pkginfo = self.archives / "w5-1.0-3.igos.tar.gz"
        with tarfile.open(no_pkginfo, "w:gz") as tf:
            info = tarfile.TarInfo("usr/share/w5/marker")
            tf.addfile(info, io.BytesIO(b""))
        corrupt = self.archives / "w6-1.0-3.igos.tar.gz"
        corrupt.write_bytes(b"not a gzip stream")
        self._seed([
            ("w1", "1.0-3", 3, str(self.archives / "w1-1.0-3.igos.tar.gz")),  # gone
            ("w2", "1.0-3", 3, other_release),
            ("w3", "1.0-3", 3, other_name),
            ("w4", "1.0-3", 3, other_version),
            ("w5", "1.0-3", 3, str(no_pkginfo)),
            ("w6", "1.0-3", 3, str(corrupt)),
            ("w7", "1.0-3", 3, None),
        ])
        before = self._rows()

        output.set_process_level(output.VERBOSE)
        db, out, err = self._open()
        db.close()

        self.assertEqual(self._rows(), before)
        self.assertEqual(self._history(), [])
        for name in ("w1", "w2", "w3", "w4", "w5", "w6", "w7"):
            said = [ln for ln in (out + err).splitlines() if f" {name} " in ln
                    or f" {name}'" in ln]
            self.assertEqual(len(said), 1, f"{name}: {out + err}")
            self.assertIn("left as it is", said[0])

    def test_a_row_left_as_it_is_says_nothing_below_verbose(self):
        self._seed([("w1", "1.0-3", 3, str(self.archives / "gone.igos.tar.gz"))])

        db, out, err = self._open()
        db.close()

        self.assertEqual((out, err), ("", ""))
        self.assertEqual(self._rows()["w1"], ("1.0-3", 3))

    def test_a_read_only_open_writes_nothing(self):
        path = self._archive("widget-1.0-3.igos.tar.gz", "widget", "1.0", 3)
        self._seed([("widget", "1.0-3", 3, path)])
        con = sqlite3.connect(self.db_path)
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.close()
        before = self.db_path.read_bytes()

        db, out, err = self._open(read_only=True)
        self.assertEqual(db.get_installed("widget")["version"], "1.0-3")
        db.close()

        self.assertEqual(self.db_path.read_bytes(), before)
        self.assertEqual((out, err), ("", ""))

    def test_the_repaired_row_is_accepted_by_the_next_forward_move(self):
        path = self._archive("intergen-0.1.0-335.igos.tar.gz",
                             "intergen", "0.1.0", 335)
        self._seed([("intergen", "0.1.0-335", 335, path)])
        candidate = {"version": "0.1.0", "release": 337}
        con = sqlite3.connect(self.db_path)
        version, release = con.execute(
            "SELECT version, release FROM installed WHERE name='intergen'").fetchone()
        con.close()
        poisoned = downgrade_decision(
            "intergen", {"version": version, "release": release}, candidate)
        self.assertEqual(poisoned.kind, "refuse", poisoned.message)

        db, _, _ = self._open()
        installed = db.get_installed("intergen")
        db.close()

        decision = downgrade_decision("intergen", installed, candidate)
        self.assertEqual(decision.kind, "proceed", decision.message)


if __name__ == "__main__":
    unittest.main()
