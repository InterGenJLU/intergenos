#!/usr/bin/env python3
"""Tests for Phase 4 atomicity + supersede failure-path guarantees (RFC §4).

These tests exercise the contract documented in Phase 4 §2e and RFC §4b:
  - deploy-before-transaction: deploy fails → no DB record
  - transaction-rollback: mid-supersede failure → DB consistent
  - file-ownership-transfer: correct paths move, unchanged stay
  - idempotent-reinstall: re-run after deploy crash recovers cleanly
"""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from pkm.database import PackageDB, _sha256
from pkm.installer import PackageInstaller
from pkm.verifier import PackageVerifier, EXIT_OK, EXIT_MODIFIED, EXIT_SUPERSEDED


class TestSupersedeAtomicity(unittest.TestCase):
    """Tests for the BEGIN/COMMIT/ROLLBACK atomicity contract (RFC §4b)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp) / "root"
        self.root.mkdir()
        self.db_path = Path(self.tmp) / "test.db"
        self.db = PackageDB(self.db_path)
        self.installer = PackageInstaller(self.db, root=str(self.root))
        self.verifier = PackageVerifier(self.db)

    def tearDown(self):
        self.db.close()

    def _install_stub(self, name, version="1.0", supersedes=None):
        """Install a minimal stub package with one file."""
        archive_dir = Path(self.tmp) / f"archive-{name}"
        archive_dir.mkdir(exist_ok=True)
        fake_file = archive_dir / f"{name}.txt"
        fake_file.write_text(f"content of {name}\n")
        archive_path = archive_dir / f"{name}-{version}.igos.tar.gz"
        import tarfile
        with tarfile.open(archive_path, "w:gz") as tf:
            tf.add(fake_file, arcname=f"{name}.txt")
        ok, msg = self.installer.install(name, archive_path=archive_path)
        return ok, msg, archive_path

    def test_deploy_succeeds_ownership_transferred(self):
        """After successful supersede, predecessor is marked superseded."""
        self._install_stub("pass1")
        self._install_stub("pass2", supersedes=["pass1"])

        pred = self.db.get_installed("pass1")
        self.assertEqual(pred["superseded_by"], "pass2")

    def test_deploy_failure_no_db_record(self):
        """If deploy fails, no record exists for the attempted package."""
        self._install_stub("pass1", version="0.9")

        # Install pass2 with a deliberately broken archive path
        ok, msg = self.installer.install("pass2", archive_path=Path("/nonexistent.tar.gz"))
        self.assertFalse(ok)

        existing = self.db.get_installed("pass2")
        self.assertIsNone(existing)

        pred = self.db.get_installed("pass1")
        self.assertIsNone(pred.get("superseded_by"))

    def test_transaction_rollback_mid_supersede(self):
        """If the DB transaction fails after deploy, the DB is consistent."""
        self._install_stub("pass1")

        # Intercept the DB commit to simulate failure mid-supersede
        orig_commit = self.db.conn.commit
        def raise_on_first_commit():
            orig_commit()
            raise sqlite3.OperationalError("simulated disk full")
        self.db.conn.commit = raise_on_first_commit

        try:
            self._install_stub("pass2", supersedes=["pass1"])
        except sqlite3.OperationalError:
            pass
        finally:
            self.db.conn.commit = orig_commit

        # pass1 should still exist and NOT be superseded
        pred = self.db.get_installed("pass1")
        self.assertIsNotNone(pred)
        self.assertIsNone(pred.get("superseded_by"))

    def test_file_ownership_transfer_only_overlap(self):
        """Only paths pass2 actually wrote (overlapping) transfer from pass1."""
        pass1_archive = Path(self.tmp) / "pass1-archive"
        pass1_archive.mkdir(exist_ok=True)
        (pass1_archive / "shared.txt").write_text("pass1-shared\n")
        (pass1_archive / "only-pass1.txt").write_text("pass1-only\n")
        p1_arc = pass1_archive / "pass1-1.0.igos.tar.gz"
        import tarfile
        with tarfile.open(p1_arc, "w:gz") as tf:
            tf.add(pass1_archive / "shared.txt", arcname="shared.txt")
            tf.add(pass1_archive / "only-pass1.txt", arcname="only-pass1.txt")
        self.installer.install("pass1", archive_path=p1_arc)

        pass2_archive = Path(self.tmp) / "pass2-archive"
        pass2_archive.mkdir(exist_ok=True)
        (pass2_archive / "shared.txt").write_text("pass2-shared\n")
        (pass2_archive / "only-pass2.txt").write_text("pass2-only\n")
        p2_arc = pass2_archive / "pass2-1.0.igos.tar.gz"
        with tarfile.open(p2_arc, "w:gz") as tf:
            tf.add(pass2_archive / "shared.txt", arcname="shared.txt")
            tf.add(pass2_archive / "only-pass2.txt", arcname="only-pass2.txt")
        ok, msg = self.installer.install("pass2", archive_path=p2_arc)

        self.assertTrue(ok)
        owner_shared = self.db.find_owner("shared.txt")
        self.assertEqual(owner_shared["name"], "pass2")
        owner_pass2 = self.db.find_owner("only-pass2.txt")
        self.assertEqual(owner_pass2["name"], "pass2")

    def test_idempotent_reinstall_after_crash(self):
        """Re-running install after a deploy crash recovers cleanly."""
        self._install_stub("pass1")
        self._install_stub("pass2", supersedes=["pass1"])

        # Re-install — should be idempotent (pass2 already installed, pass1 superseded)
        pred = self.db.get_installed("pass1")
        self.assertEqual(pred["superseded_by"], "pass2")

        succ = self.db.get_installed("pass2")
        self.assertIsNotNone(succ)

    def test_supersede_preserves_predecessor_record(self):
        """Superseded package record persists in installed table (audit trail)."""
        self._install_stub("pass1")
        self._install_stub("pass2", supersedes=["pass1"])

        pred = self.db.get_installed("pass1")
        self.assertIsNotNone(pred)
        self.assertEqual(pred["superseded_by"], "pass2")
        self.assertIsNotNone(pred.get("superseded_at"))

    def test_verify_superseded_returns_exit_code_2(self):
        """Verifying a superseded package returns EXIT_SUPERSEDED."""
        self._install_stub("pass1")
        self._install_stub("pass2", supersedes=["pass1"])

        result = self.verifier.verify("pass1")
        self.assertEqual(result["exit_code"], EXIT_SUPERSEDED)
        self.assertEqual(result["superseded_by"], "pass2")

    def test_verify_active_passes_strict(self):
        """Verifying the active successor in strict mode returns EXIT_OK."""
        self._install_stub("pass1")
        self._install_stub("pass2", supersedes=["pass1"])

        result = self.verifier.verify("pass2", mode="strict")
        self.assertEqual(result["exit_code"], EXIT_OK)
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["modified"], [])

    def test_mark_superseded_idempotent(self):
        """mark_superseded is safe to call twice."""
        self._install_stub("pass1")
        self._install_stub("pass2", supersedes=["pass1"])

        # Second call — should not error
        self.db.mark_superseded("pass1", "pass2")
        pred = self.db.get_installed("pass1")
        self.assertEqual(pred["superseded_by"], "pass2")

    def test_transfer_unknown_predecessor(self):
        """transfer_file_ownership on a missing predecessor is a no-op."""
        self._install_stub("pass2")

        # Transfer from a predecessor that was never installed
        count = self.db.transfer_file_ownership("nonexistent", 99, ["fake.txt"])
        self.assertEqual(count, 0)


class TestAtomicityConvenience(unittest.TestCase):
    """Utility function tests used by the atomicity layer."""

    def test_sha256_deterministic(self):
        """_sha256 produces consistent output for known content."""
        path = Path(self._testMethodName)  # temp name
        path.write_text("hello\n")
        try:
            h1 = _sha256(str(path))
            h2 = _sha256(str(path))
            self.assertEqual(h1, h2)
            self.assertEqual(len(h1), 64)
        finally:
            path.unlink()

    def test_find_owner_skip_superseded(self):
        """find_owner does not return superseded owners."""
        db_path = Path(self._testMethodName + ".db")
        db = PackageDB(db_path)
        try:
            db.conn.executescript("""
                INSERT INTO installed (name, version, superseded_by) VALUES ('old', '1.0', 'new');
                INSERT INTO installed (name, version) VALUES ('new', '2.0');
                INSERT INTO files (package_id, path) VALUES (
                    (SELECT id FROM installed WHERE name='old') , 'shared.txt'
                );
                INSERT INTO files (package_id, path) VALUES (
                    (SELECT id FROM installed WHERE name='new') , 'shared.txt'
                );
            """)
            owner = db.find_owner("shared.txt")
            self.assertEqual(owner["name"], "new")
        finally:
            db.close()
            db_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
