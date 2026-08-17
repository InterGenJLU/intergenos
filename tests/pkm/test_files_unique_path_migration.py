#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""One ownership row per path per package, enforced by the database.

add_files has always used INSERT OR REPLACE, and two comments in it reason
from a UNIQUE(package_id, path) constraint — one says the statement "already
absorbs the benign duplicate case", the other explains what an IntegrityError
would mean given that. The constraint was never declared. Without a conflict
target, INSERT OR REPLACE degrades to a plain INSERT, so every re-registration
of an already-owned path appended ANOTHER row: a helper re-run duplicated its
entire payload, and remove, verify and the shipping-tree ownership gate each
counted the same file more than once.

The constraint is now declared, and a pre-existing database gets it through a
rebuild — SQLite cannot add a table constraint in place. The rebuild collapses
duplicates it finds, keeping the newest row for each (package_id, path),
because the newest row is the one the most recent install wrote and therefore
the one whose checksum matches what is on disk.

The migration runs against LIVE installed databases, so these tests care as
much about what it must NOT do — lose rows for other packages, lose a source
label, lose config baselines, run twice — as about the duplicates it collapses.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from pkm.database import PackageDB


def _legacy_files_table(path: Path) -> None:
    """A files table exactly as it was BEFORE the constraint: no UNIQUE."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE installed (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            version TEXT NOT NULL,
            release INTEGER DEFAULT 1,
            UNIQUE(name)
        );
        CREATE TABLE files (
            id INTEGER PRIMARY KEY,
            package_id INTEGER NOT NULL REFERENCES installed(id) ON DELETE CASCADE,
            path TEXT NOT NULL,
            is_dir BOOLEAN DEFAULT 0,
            is_config BOOLEAN DEFAULT 0,
            checksum TEXT,
            is_generated INTEGER DEFAULT 0,
            source TEXT
        );
        """
    )
    conn.commit()
    conn.close()


class UniquePathMigrationTest(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.db_path = self.tmp / "pkm.db"

    def tearDown(self):
        self._td.cleanup()

    def _seed(self, rows):
        """rows: (package_id, path, checksum, source)."""
        _legacy_files_table(self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO installed (id, name, version) VALUES (1, 'vscode', '1.0')")
        conn.execute("INSERT INTO installed (id, name, version) VALUES (2, 'bash', '5.2')")
        for pkg_id, path, checksum, source in rows:
            conn.execute(
                "INSERT INTO files (package_id, path, checksum, source) "
                "VALUES (?, ?, ?, ?)", (pkg_id, path, checksum, source))
        conn.commit()
        conn.close()

    def _open(self):
        return PackageDB(self.db_path, root=str(self.tmp / "root"))

    def _rows(self, db, pkg_id=1):
        return db.conn.execute(
            "SELECT path, checksum, source FROM files WHERE package_id = ? "
            "ORDER BY path", (pkg_id,)).fetchall()

    # -- the defect itself ------------------------------------------------

    def test_legacy_table_really_does_accept_duplicates(self):
        """The premise. If this ever stops being true the rest is theatre."""
        self._seed([(1, "opt/vscode/code", "aaa", "helper"),
                    (1, "opt/vscode/code", "bbb", "helper")])
        conn = sqlite3.connect(self.db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM files WHERE path = 'opt/vscode/code'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 2)

    def test_duplicates_are_collapsed_to_the_newest_row(self):
        self._seed([(1, "opt/vscode/code", "old-hash", "helper"),
                    (1, "opt/vscode/code", "new-hash", "helper"),
                    (1, "opt/vscode/bin/code", "only", "helper")])
        db = self._open()
        self.addCleanup(db.close)
        rows = self._rows(db)
        self.assertEqual([r[0] for r in rows],
                         ["opt/vscode/bin/code", "opt/vscode/code"])
        self.assertEqual(dict((r[0], r[1]) for r in rows)["opt/vscode/code"],
                         "new-hash")

    def test_constraint_is_present_afterwards(self):
        self._seed([(1, "opt/vscode/code", "x", None)])
        db = self._open()
        self.addCleanup(db.close)
        self.assertTrue(db._files_has_unique_path())
        with self.assertRaises(sqlite3.IntegrityError):
            db.conn.execute(
                "INSERT INTO files (package_id, path) VALUES (1, 'opt/vscode/code')")

    def test_add_files_now_replaces_instead_of_appending(self):
        """What the comment in add_files always claimed."""
        self._seed([(1, "opt/vscode/code", "old", "helper")])
        db = self._open()
        self.addCleanup(db.close)
        for _ in range(3):
            db.add_files(1, ["opt/vscode/code"], hashes={"opt/vscode/code": "new"},
                         source="helper")
        rows = self._rows(db)
        self.assertEqual(len(rows), 1, f"duplicate rows returned: {rows}")
        self.assertEqual(rows[0][1], "new")

    # -- what it must NOT do ----------------------------------------------

    def test_other_packages_rows_survive_untouched(self):
        self._seed([(1, "opt/vscode/code", "a", "helper"),
                    (1, "opt/vscode/code", "b", "helper"),
                    (2, "usr/bin/bash", "c", "archive"),
                    (2, "usr/share/man/man1/bash.1", "d", "archive")])
        db = self._open()
        self.addCleanup(db.close)
        self.assertEqual(
            [r[0] for r in self._rows(db, pkg_id=2)],
            ["usr/bin/bash", "usr/share/man/man1/bash.1"])
        self.assertEqual({r[2] for r in self._rows(db, pkg_id=2)}, {"archive"})

    def test_a_source_label_is_not_lost_when_the_newest_row_lacks_one(self):
        """The newest row wins on content, but an ownership label it does not
        carry is inherited rather than dropped — an unlabelled payload row
        cannot be replaced by the next helper run."""
        self._seed([(1, "opt/vscode/code", "old", "helper"),
                    (1, "opt/vscode/code", "new", None)])
        db = self._open()
        self.addCleanup(db.close)
        rows = self._rows(db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "new")
        self.assertEqual(rows[0][2], "helper")

    def test_no_rows_are_lost_when_there_are_no_duplicates(self):
        paths = [f"usr/share/demo/{i}" for i in range(50)]
        self._seed([(1, p, f"h{i}", "archive") for i, p in enumerate(paths)])
        db = self._open()
        self.addCleanup(db.close)
        self.assertEqual(len(self._rows(db)), 50)

    def test_running_twice_is_a_no_op(self):
        self._seed([(1, "opt/vscode/code", "a", "helper"),
                    (1, "opt/vscode/code", "b", "helper")])
        db = self._open()
        first = self._rows(db)
        db.close()
        db2 = self._open()
        self.addCleanup(db2.close)
        self.assertEqual(self._rows(db2), first)
        self.assertEqual(db2._migrate_files_unique_path(), 0)

    def test_a_fresh_database_gets_the_constraint_without_a_rebuild(self):
        db = PackageDB(self.tmp / "fresh.db", root=str(self.tmp / "root"))
        self.addCleanup(db.close)
        self.assertTrue(db._files_has_unique_path())
        self.assertEqual(db._migrate_files_unique_path(), 0)

    def test_foreign_key_cascade_still_works_after_the_rebuild(self):
        """The rebuilt table has to keep the ON DELETE CASCADE that makes
        removing a package take its ownership rows with it."""
        self._seed([(1, "opt/vscode/code", "a", "helper"),
                    (1, "opt/vscode/code", "b", "helper")])
        db = self._open()
        self.addCleanup(db.close)
        db.conn.execute("PRAGMA foreign_keys=ON")
        db.conn.execute("DELETE FROM installed WHERE id = 1")
        db.conn.commit()
        self.assertEqual(self._rows(db), [])

    def test_indexes_are_recreated(self):
        self._seed([(1, "opt/vscode/code", "a", None)])
        db = self._open()
        self.addCleanup(db.close)
        names = {r[0] for r in db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='files'").fetchall()}
        self.assertIn("idx_files_path", names)
        self.assertIn("idx_files_package", names)


if __name__ == "__main__":
    unittest.main()


class SupersedeOverlapTest(unittest.TestCase):
    """A supersede with overlapping paths must leave ONE ownership row.

    This is the defect the constraint exposed rather than caused. During an
    atomic supersede the successor installs first, writing its own row for
    every path it ships; transfer_file_ownership then re-pointed the
    predecessor's row for the OVERLAPPING paths onto the successor — on top of
    the row that was already there. With no uniqueness constraint that
    appended a second row and nobody noticed; remove, verify and the ownership
    gate then each counted the same file twice.
    """

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.tmp / "root"))

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def test_overlapping_path_ends_with_one_row_owned_by_the_successor(self):
        pred = self.db.add_installed("pass1", "1.0")
        self.db.add_files(pred, ["usr/lib/shared.so", "usr/lib/only-pass1.so"],
                          hashes={"usr/lib/shared.so": "old"})
        succ = self.db.add_installed("pass2", "2.0")
        self.db.add_files(succ, ["usr/lib/shared.so", "usr/lib/only-pass2.so"],
                          hashes={"usr/lib/shared.so": "new"})

        self.db.transfer_file_ownership(
            "pass1", succ, ["usr/lib/shared.so"],
            hashes={"usr/lib/shared.so": "new"})

        rows = self.db.conn.execute(
            "SELECT package_id, checksum FROM files WHERE path = 'usr/lib/shared.so'"
        ).fetchall()
        self.assertEqual(len(rows), 1, f"duplicate ownership rows: {rows}")
        self.assertEqual(rows[0][0], succ)
        self.assertEqual(rows[0][1], "new")

    def test_predecessor_keeps_paths_the_successor_never_wrote(self):
        pred = self.db.add_installed("pass1", "1.0")
        self.db.add_files(pred, ["usr/lib/shared.so", "usr/lib/only-pass1.so"])
        succ = self.db.add_installed("pass2", "2.0")
        self.db.add_files(succ, ["usr/lib/shared.so"])
        self.db.transfer_file_ownership("pass1", succ, ["usr/lib/shared.so"])
        still = self.db.conn.execute(
            "SELECT package_id FROM files WHERE path = 'usr/lib/only-pass1.so'"
        ).fetchall()
        self.assertEqual([r[0] for r in still], [pred])

    def test_transfer_of_a_path_the_successor_does_not_own_still_moves_it(self):
        pred = self.db.add_installed("pass1", "1.0")
        self.db.add_files(pred, ["usr/lib/moved.so"])
        succ = self.db.add_installed("pass2", "2.0")
        moved = self.db.transfer_file_ownership("pass1", succ, ["usr/lib/moved.so"])
        self.assertEqual(moved, 1)
        owner = self.db.conn.execute(
            "SELECT package_id FROM files WHERE path = 'usr/lib/moved.so'"
        ).fetchone()[0]
        self.assertEqual(owner, succ)

    def test_transfer_count_is_a_count_of_paths_not_of_connection_writes(self):
        """conn.total_changes is cumulative for the connection; the old
        accounting returned it, so the number grew with unrelated writes."""
        pred = self.db.add_installed("pass1", "1.0")
        self.db.add_files(pred, ["a/one", "a/two", "a/three"])
        succ = self.db.add_installed("pass2", "2.0")
        moved = self.db.transfer_file_ownership("pass1", succ, ["a/one", "a/two"])
        self.assertEqual(moved, 2)
