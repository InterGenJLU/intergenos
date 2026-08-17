"""B7/S-D 2 (USA-1 walk closure): PackageDB.__init__ create_if_missing semantics.

The security-class audit-tool MASK fix. Before d60eca20, PackageDB.__init__
unconditionally bootstrapped the SQLite file on every import, meaning audit
tools that just constructed a PackageDB silently materialized an empty DB at
the canonical path and `pkm verify --all` against a never-populated system
returned EXIT_OK with 0 packages — pass-by-vacuity.

The kwarg lets READ paths (verify/list/files/etc.) surface FileNotFoundError
with a clear diagnostic, while WRITE paths (install/install-helper/import)
still auto-create the DB on first run.
"""

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pkm.database import PackageDB  # noqa: E402


class CreateIfMissingTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "pkm.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_creates_db_when_missing(self):
        self.assertFalse(self.db_path.exists())
        db = PackageDB(self.db_path)
        try:
            self.assertTrue(self.db_path.exists())
        finally:
            db.close()

    def test_create_if_missing_true_creates_db_when_missing(self):
        self.assertFalse(self.db_path.exists())
        db = PackageDB(self.db_path, create_if_missing=True)
        try:
            self.assertTrue(self.db_path.exists())
        finally:
            db.close()

    def test_create_if_missing_false_raises_on_missing_db(self):
        self.assertFalse(self.db_path.exists())
        with self.assertRaises(FileNotFoundError) as ctx:
            PackageDB(self.db_path, create_if_missing=False)
        msg = str(ctx.exception)
        self.assertIn("does not exist", msg)
        self.assertIn("create_if_missing=False", msg)
        self.assertFalse(self.db_path.exists())

    def test_create_if_missing_false_opens_existing_db(self):
        seed = PackageDB(self.db_path)
        seed.close()
        self.assertTrue(self.db_path.exists())
        db = PackageDB(self.db_path, create_if_missing=False)
        try:
            row = db.conn.execute(
                "SELECT count(*) FROM installed"
            ).fetchone()
            self.assertEqual(row[0], 0)
        finally:
            db.close()


class ReadOnlyOpenTests(unittest.TestCase):
    """PackageDB(read_only=True) — the installed-system inspection path.

    On an installed system /var/lib/igos/pkm.db is root-owned; the normal open
    path runs `PRAGMA journal_mode = WAL` (a WRITE), so a regular user running
    a pure-read command (`pkm list`, `info`, `files`, ...) hit "attempt to
    write a readonly database" and could not inspect their own machine without
    sudo. read_only=True opens immutable — no write to the db, its -wal/-shm
    sidecars, or the parent dir. Prime Directive: read your own system without
    root.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "pkm.db"
        # Seed a real db via the normal write path, then close it.
        seed = PackageDB(self.db_path)
        seed.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_only_opens_and_selects(self):
        db = PackageDB(self.db_path, read_only=True)
        try:
            row = db.conn.execute("SELECT count(*) FROM installed").fetchone()
            self.assertEqual(row[0], 0)
        finally:
            db.close()

    def test_read_only_rejects_writes(self):
        import sqlite3
        db = PackageDB(self.db_path, read_only=True)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                db.conn.execute(
                    "INSERT INTO installed (name, version) VALUES ('x', '1')"
                )
        finally:
            db.close()

    def test_read_only_missing_db_raises(self):
        missing = Path(self.tmp.name) / "nope.db"
        with self.assertRaises(FileNotFoundError):
            PackageDB(missing, read_only=True)

    def test_read_only_creates_no_wal_sidecar(self):
        # The crux of the non-root fix: opening read-only must not write the
        # -wal/-shm sidecars (which a non-root user cannot create next to a
        # root-owned db).
        db = PackageDB(self.db_path, read_only=True)
        try:
            db.conn.execute("SELECT count(*) FROM installed").fetchone()
            self.assertFalse((Path(str(self.db_path) + "-wal")).exists())
            self.assertFalse((Path(str(self.db_path) + "-shm")).exists())
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
