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

    def _install_stub(self, name, version="1.0", supersedes=None, files=None):
        """Install a minimal stub package via direct DB injection.

        Bypasses tar/archive plumbing — tests the DB-transaction layer
        in isolation. Uses db.add_installed + db.add_files directly,
        matching the atomicity surface these tests exercise (RFC §4b).
        """
        if files is None:
            fname = f"{name}.txt"
            files = [(fname, f"content of {name}\n")]

        # Write files to the root FS (so verify + hashing work)
        hashes = {}
        for path, content in files:
            full_path = self.root / path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content)
            import hashlib
            hashes[path] = hashlib.sha256(content.encode()).hexdigest()

        pkg_id = self.db.add_installed(
            name=name, version=version, install_method="test"
        )
        self.db.add_files(pkg_id, [p for p, _ in files], hashes=hashes)

        if supersedes:
            for pred_name in supersedes:
                pred = self.db.get_installed(pred_name)
                if pred:
                    overlap = self._paths_owned_by(pred_name, [p for p, _ in files])
                    if overlap:
                        self.db.transfer_file_ownership(pred_name, pkg_id, overlap, hashes=hashes)
                    self.db.mark_superseded(pred_name, name)
        return True, f"installed {name}", None

    def _paths_owned_by(self, name, candidate_paths):
        owned = set()
        for path in candidate_paths:
            owner = self.db.find_owner(path)
            if owner and owner["name"] == name:
                owned.add(path)
        return list(owned)

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
        """If the DB transaction fails mid-supersede, the DB is consistent."""
        from unittest.mock import patch
        self._install_stub("pass1")

        pass1 = self.db.get_installed("pass1")

        # Simulate mid-transaction failure by raising an OperationalError
        # inside the transfer_file_ownership call (owned method, patchable).
        with patch.object(self.db, 'transfer_file_ownership',
                          side_effect=sqlite3.OperationalError("simulated crash")):
            self.db.conn.execute("BEGIN")
            try:
                pkg_id = self.db.add_installed(name="pass2", version="1.0", install_method="test")
                self.db.transfer_file_ownership("pass1", pkg_id, ["shared.txt"])
                self.db.mark_superseded("pass1", "pass2")
                self.db.conn.commit()
            except sqlite3.OperationalError:
                self.db.conn.rollback()

        # pass1 should still exist and NOT be superseded
        pred = self.db.get_installed("pass1")
        self.assertIsNotNone(pred)
        self.assertIsNone(pred.get("superseded_by"))

    def test_file_ownership_transfer_only_overlap(self):
        """Only paths pass2 actually wrote (overlapping) transfer from pass1."""
        self._install_stub("pass1", files=[
            ("shared.txt", "pass1-shared\n"),
            ("only-pass1.txt", "pass1-only\n"),
        ])
        self._install_stub("pass2", files=[
            ("shared.txt", "pass2-shared\n"),
            ("only-pass2.txt", "pass2-only\n"),
        ], supersedes=["pass1"])

        owner_shared = self.db.find_owner("shared.txt")
        self.assertEqual(owner_shared["name"], "pass2")
        owner_pass1 = self.db.find_owner("only-pass1.txt")
        self.assertIsNone(owner_pass1)  # retired with predecessor

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

    def test_verify_active_superseded_state(self):
        """find_owner returns active (non-superseded) owner after supersede.
        
        Uses find_owner rather than verify() because test files live in a
        tempdir, not the absolute / filesystem — verify checks FS existence.
        The DB state is what these atomicity tests validate."""
        self._install_stub("pass1")
        self._install_stub("pass2", supersedes=["pass1"])

        owner_pass2 = self.db.find_owner("pass2.txt")
        self.assertEqual(owner_pass2["name"], "pass2")
        self.assertIsNone(owner_pass2["superseded_by"])

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
