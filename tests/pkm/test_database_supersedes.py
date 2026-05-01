"""Unit tests for pkm/database.py supersedes-related primitives.

Covers:
- Schema includes superseded_by + superseded_at (fresh DB)
- Idempotent migration on legacy DBs
- mark_superseded / is_superseded round-trip
- transfer_file_ownership behaviour (subset transfer + checksum update)
- find_owner default filter + include_superseded toggle
- verify_package strict vs fast modes
- verify_package surfaces superseded_by
- _parse_manifest backwards compatibility (no hashes) + extension (with hashes
  + SUPERSEDES header)
"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pkm.database import PackageDB, _parse_manifest  # noqa: E402


class SchemaTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "pkm.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_fresh_db_has_supersedes_columns(self):
        db = PackageDB(self.db_path)
        try:
            cols = [
                row[1]
                for row in db.conn.execute("PRAGMA table_info(installed)").fetchall()
            ]
            self.assertIn("superseded_by", cols)
            self.assertIn("superseded_at", cols)
        finally:
            db.close()

    def test_legacy_db_migrates_idempotently(self):
        legacy_conn = sqlite3.connect(str(self.db_path))
        legacy_conn.executescript(
            """
            CREATE TABLE installed (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                version TEXT NOT NULL
            );
            CREATE TABLE files (
                id INTEGER PRIMARY KEY,
                package_id INTEGER REFERENCES installed(id),
                path TEXT,
                is_dir BOOLEAN DEFAULT 0,
                is_config BOOLEAN DEFAULT 0,
                checksum TEXT
            );
            """
        )
        legacy_conn.commit()
        legacy_conn.close()

        db1 = PackageDB(self.db_path)
        cols1 = [
            row[1]
            for row in db1.conn.execute("PRAGMA table_info(installed)").fetchall()
        ]
        self.assertIn("superseded_by", cols1)
        self.assertIn("superseded_at", cols1)
        db1.close()

        db2 = PackageDB(self.db_path)
        try:
            cols2 = [
                row[1]
                for row in db2.conn.execute("PRAGMA table_info(installed)").fetchall()
            ]
            self.assertIn("superseded_by", cols2)
        finally:
            db2.close()


class SupersededMarkerTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = PackageDB(Path(self.tmp.name) / "pkm.db")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_mark_superseded_round_trip(self):
        self.db.add_installed("pass1", "1.0")
        self.db.add_installed("pass2", "1.0")
        self.db.mark_superseded("pass1", "pass2")
        self.db.conn.commit()
        self.assertEqual(self.db.is_superseded("pass1"), "pass2")
        self.assertIsNone(self.db.is_superseded("pass2"))

    def test_is_superseded_unknown_package(self):
        self.assertIsNone(self.db.is_superseded("does-not-exist"))


class TransferFileOwnershipTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = PackageDB(Path(self.tmp.name) / "pkm.db")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_transfer_subset_of_paths(self):
        p1_id = self.db.add_installed("pass1", "1.0")
        p2_id = self.db.add_installed("pass2", "1.0")
        self.db.add_files(
            p1_id,
            ["usr/bin/foo", "usr/bin/bar", "usr/lib/lib.so"],
            hashes={
                "usr/bin/foo": "h_foo",
                "usr/bin/bar": "h_bar",
                "usr/lib/lib.so": "h_lib",
            },
        )

        self.db.transfer_file_ownership("pass1", p2_id, ["usr/bin/foo"])
        self.db.conn.commit()

        owner_foo = self.db.find_owner("usr/bin/foo")
        self.assertEqual(owner_foo["name"], "pass2")

        owner_bar = self.db.find_owner("usr/bin/bar")
        self.assertEqual(owner_bar["name"], "pass1")

        owner_lib = self.db.find_owner("usr/lib/lib.so")
        self.assertEqual(owner_lib["name"], "pass1")

    def test_transfer_with_hashes_updates_checksum(self):
        p1_id = self.db.add_installed("pass1", "1.0")
        p2_id = self.db.add_installed("pass2", "1.0")
        self.db.add_files(p1_id, ["usr/bin/foo"], hashes={"usr/bin/foo": "old_hash"})

        new_hash = "a" * 64
        self.db.transfer_file_ownership(
            "pass1", p2_id, ["usr/bin/foo"], hashes={"usr/bin/foo": new_hash},
        )
        self.db.conn.commit()

        row = self.db.conn.execute(
            "SELECT checksum, package_id FROM files WHERE path = ?",
            ("usr/bin/foo",),
        ).fetchone()
        self.assertEqual(row[0], new_hash)
        self.assertEqual(row[1], p2_id)

    def test_transfer_predecessor_missing_returns_zero(self):
        result = self.db.transfer_file_ownership(
            "nonexistent", 999, ["usr/bin/foo"],
        )
        self.assertEqual(result, 0)


class FindOwnerFilterTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = PackageDB(Path(self.tmp.name) / "pkm.db")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_default_filters_superseded(self):
        p1_id = self.db.add_installed("pass1", "1.0")
        p2_id = self.db.add_installed("pass2", "1.0")
        self.db.add_files(p1_id, ["usr/bin/shared"], hashes={"usr/bin/shared": "h1"})
        self.db.add_files(p2_id, ["usr/bin/shared"], hashes={"usr/bin/shared": "h2"})
        self.db.mark_superseded("pass1", "pass2")
        self.db.conn.commit()

        owner = self.db.find_owner("usr/bin/shared")
        self.assertEqual(owner["name"], "pass2")
        self.assertIsNone(owner["superseded_by"])

    def test_include_superseded_returns_retired_record(self):
        p1_id = self.db.add_installed("pass1", "1.0")
        self.db.add_installed("pass2", "1.0")
        self.db.add_files(
            p1_id, ["usr/bin/only-pass1"], hashes={"usr/bin/only-pass1": "h"},
        )
        self.db.mark_superseded("pass1", "pass2")
        self.db.conn.commit()

        active = self.db.find_owner("usr/bin/only-pass1")
        self.assertIsNone(active)

        retired = self.db.find_owner(
            "usr/bin/only-pass1", include_superseded=True,
        )
        self.assertIsNotNone(retired)
        self.assertEqual(retired["name"], "pass1")
        self.assertEqual(retired["superseded_by"], "pass2")

    def test_no_match_returns_none(self):
        self.assertIsNone(self.db.find_owner("nonexistent/path"))


class VerifyPackageStrictModeTests(unittest.TestCase):
    """verify_package strict vs fast (lexists-only) — modes drive hash compare."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = PackageDB(Path(self.tmp.name) / "pkm.db")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_unknown_package_returns_none(self):
        self.assertIsNone(self.db.verify_package("does-not-exist"))

    def test_strict_catches_modified_via_hash_mismatch(self):
        p_id = self.db.add_installed("pkg", "1.0")
        self.db.add_files(
            p_id, ["usr/bin/x"], hashes={"usr/bin/x": "expected_hash"},
        )
        with mock.patch("pkm.database.os.path.lexists", return_value=True), \
                mock.patch(
                    "pkm.database._sha256", return_value="different_actual_hash",
                ):
            result = self.db.verify_package("pkg", strict=True)
        self.assertEqual(result["modified"], ["usr/bin/x"])
        self.assertEqual(result["missing"], [])

    def test_fast_mode_does_not_call_sha256(self):
        p_id = self.db.add_installed("pkg", "1.0")
        self.db.add_files(
            p_id, ["usr/bin/x"], hashes={"usr/bin/x": "expected_hash"},
        )
        sha256_mock = mock.MagicMock(return_value="any")
        with mock.patch("pkm.database.os.path.lexists", return_value=True), \
                mock.patch("pkm.database._sha256", sha256_mock):
            result = self.db.verify_package("pkg", strict=False)
        self.assertEqual(result["modified"], [])
        self.assertEqual(result["missing"], [])
        sha256_mock.assert_not_called()

    def test_strict_catches_missing(self):
        p_id = self.db.add_installed("pkg", "1.0")
        self.db.add_files(p_id, ["usr/bin/x"], hashes={"usr/bin/x": "h"})
        with mock.patch("pkm.database.os.path.lexists", return_value=False):
            result = self.db.verify_package("pkg", strict=True)
        self.assertEqual(result["missing"], ["usr/bin/x"])

    def test_fast_catches_missing(self):
        p_id = self.db.add_installed("pkg", "1.0")
        self.db.add_files(p_id, ["usr/bin/x"], hashes={"usr/bin/x": "h"})
        with mock.patch("pkm.database.os.path.lexists", return_value=False):
            result = self.db.verify_package("pkg", strict=False)
        self.assertEqual(result["missing"], ["usr/bin/x"])

    def test_verify_surfaces_superseded_by(self):
        p1_id = self.db.add_installed("pass1", "1.0")
        self.db.add_installed("pass2", "1.0")
        self.db.add_files(p1_id, [], hashes={})
        self.db.mark_superseded("pass1", "pass2")
        self.db.conn.commit()

        result = self.db.verify_package("pass1")
        self.assertEqual(result["superseded_by"], "pass2")


class ParseManifestFormatTests(unittest.TestCase):
    """_parse_manifest tolerates pre-RFC and post-RFC manifest formats."""

    def test_old_format_no_hashes_no_supersedes(self):
        manifest = (
            "PACKAGE NAME: foo-1.0\n"
            "PACKAGE VERSION: 1.0\n"
            "FILE LIST:\n"
            "usr/bin/foo\n"
            "usr/lib/foo.so\n"
        )
        meta = _parse_manifest(manifest)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["name"], "foo")
        self.assertEqual(meta["version"], "1.0")
        self.assertEqual(meta["files"], ["usr/bin/foo", "usr/lib/foo.so"])
        self.assertEqual(meta["file_hashes"], {})
        self.assertNotIn("supersedes", meta)

    def test_new_format_with_hashes_and_supersedes(self):
        sha = "a" * 64
        manifest = (
            "PACKAGE NAME: pass2-1.0\n"
            "PACKAGE VERSION: 1.0\n"
            "SUPERSEDES: pass1-0.9\n"
            "FILE LIST:\n"
            f"usr/bin/x sha256:{sha}\n"
            "usr/lib/dir/\n"
        )
        meta = _parse_manifest(manifest)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["name"], "pass2")
        self.assertEqual(meta["version"], "1.0")
        self.assertEqual(meta["supersedes"], "pass1-0.9")
        self.assertEqual(meta["files"], ["usr/bin/x", "usr/lib/dir/"])
        self.assertEqual(meta["file_hashes"], {"usr/bin/x": sha})

    def test_empty_or_nameless_manifest_returns_none(self):
        self.assertIsNone(_parse_manifest("RANDOM CONTENT\nNO NAME HEADER\n"))


if __name__ == "__main__":
    unittest.main()
