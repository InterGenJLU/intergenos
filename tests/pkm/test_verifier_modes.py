"""Tests for pkm/verifier.py — strict/fast modes + supersede-state routing.

Covers RFC §5 verifier behavior + the Phase 4 implementation at
`feat(supersedes): Phase 4 installer + verifier` (master `c9534f7`).

Run from repo root: `python3 -m unittest tests.pkm.test_verifier_modes`

Each test is self-contained: spins up a fresh SQLite DB in a temp
directory, writes test files to that same temp tree (so `verify_package`'s
`os.path.lexists("/" + path)` resolves correctly against actual files),
populates the DB via `add_installed` + `add_files(hashes=...)` to avoid
relying on the install path. The supersede transition uses
`mark_superseded` + `transfer_file_ownership` directly — same DB
calls the Phase 4 installer makes inside its atomic transaction.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Repo root on sys.path so `import pkm` works when running
# `python3 -m unittest tests.pkm.test_verifier_modes` from repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pkm.database import PackageDB
from pkm.verifier import (
    PackageVerifier,
    EXIT_OK,
    EXIT_MODIFIED,
    EXIT_SUPERSEDED,
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _VerifierTestBase(unittest.TestCase):
    """Common setup: per-test temp tree + isolated SQLite DB + verifier."""

    def setUp(self):
        # tempfile.mkdtemp gives an absolute path under /tmp; the DB
        # stores file paths relative to root and `verify_package`
        # reconstructs the absolute path via `"/" + path`. That lines
        # up with our actual files on disk because the temp paths
        # already start with /tmp/...
        self._tempdir = tempfile.mkdtemp(prefix="pkm-verifier-test-")
        self.tempdir = Path(self._tempdir)
        db_path = self.tempdir / "pkm.db"
        self.db = PackageDB(db_path=str(db_path))
        self.verifier = PackageVerifier(self.db)

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        # Remove the temp tree. Errors swallowed so a single test failure
        # doesn't cascade into teardown noise.
        import shutil
        shutil.rmtree(self._tempdir, ignore_errors=True)

    def _write_file(self, relative_path: str, content: bytes) -> str:
        """Write a file at <tempdir>/<relative_path>; return DB-shape relative path.

        DB-shape relative path strips a leading "/" (matching the convention
        in `pkm/installer.py`'s file_list collection). The actual file lives
        at an absolute path that begins with the tempdir; verifier's
        `"/" + path` reconstruction lands at the same absolute path.
        """
        abs_path = self.tempdir / relative_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(content)
        # The DB key is the absolute path with the leading "/" stripped,
        # so the verifier's reconstruction reaches the same file.
        return str(abs_path).lstrip("/")

    def _install_package(self, name: str, version: str, files: dict[str, bytes]):
        """Register a package + its files in the DB.

        files: dict mapping relative_path → content bytes. Each path is
        written to disk under the tempdir AND added to the package's
        manifest with a sha256 computed from the bytes.
        """
        path_keys = []
        hashes = {}
        for relative_path, content in files.items():
            path_key = self._write_file(relative_path, content)
            path_keys.append(path_key)
            hashes[path_key] = _sha256_bytes(content)
        pkg_id = self.db.add_installed(
            name=name, version=version, install_method="archive",
        )
        self.db.add_files(pkg_id, path_keys, hashes=hashes)
        return pkg_id, path_keys, hashes


class TestStrictMode(_VerifierTestBase):
    """--strict catches both missing and modified files."""

    def test_strict_catches_modified_content(self):
        original = b"hello world\n"
        _, path_keys, _ = self._install_package(
            "test-pkg-modified", "1.0", {"data/strict-mod.txt": original},
        )
        # Mutate the file's content on disk while keeping its DB-recorded hash.
        target = self.tempdir / "data" / "strict-mod.txt"
        target.write_bytes(b"goodbye world\n")
        result = self.verifier.verify("test-pkg-modified", mode="strict")
        self.assertIsNotNone(result)
        self.assertEqual(result["modified"], path_keys)
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["exit_code"], EXIT_MODIFIED)

    def test_strict_catches_missing_files(self):
        _, path_keys, _ = self._install_package(
            "test-pkg-missing-strict", "1.0",
            {"data/strict-miss.txt": b"present at install time\n"},
        )
        # Delete the file on disk; DB still has the record.
        target = self.tempdir / "data" / "strict-miss.txt"
        target.unlink()
        result = self.verifier.verify("test-pkg-missing-strict", mode="strict")
        self.assertIsNotNone(result)
        self.assertEqual(result["missing"], path_keys)
        self.assertEqual(result["modified"], [])
        self.assertEqual(result["exit_code"], EXIT_MODIFIED)


class TestFastMode(_VerifierTestBase):
    """--fast skips content hashing; existence-only check."""

    def test_fast_does_not_catch_modified_content(self):
        original = b"hello world\n"
        _, _, _ = self._install_package(
            "test-pkg-fast-mod", "1.0", {"data/fast-mod.txt": original},
        )
        # Mutate content; --fast should not notice.
        target = self.tempdir / "data" / "fast-mod.txt"
        target.write_bytes(b"different bytes; same path\n")
        result = self.verifier.verify("test-pkg-fast-mod", mode="fast")
        self.assertIsNotNone(result)
        self.assertEqual(result["modified"], [])
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["exit_code"], EXIT_OK)

    def test_fast_catches_missing_files(self):
        _, path_keys, _ = self._install_package(
            "test-pkg-fast-miss", "1.0",
            {"data/fast-miss.txt": b"present at install time\n"},
        )
        target = self.tempdir / "data" / "fast-miss.txt"
        target.unlink()
        result = self.verifier.verify("test-pkg-fast-miss", mode="fast")
        self.assertIsNotNone(result)
        self.assertEqual(result["missing"], path_keys)
        self.assertEqual(result["exit_code"], EXIT_MODIFIED)


class TestSupersedeRouting(_VerifierTestBase):
    """Verify of a superseded package routes to the active successor."""

    def _install_pass1_pass2(self):
        """Install a pass1 package, then a pass2 that supersedes it.
        Returns (pass2_id, shared_path_key, content) for assertion plumbing.
        """
        # pass1 owns the file
        pass1_id, pass1_paths, pass1_hashes = self._install_package(
            "supersede-pass1", "1.0",
            {"data/shared.txt": b"pass1 content\n"},
        )
        shared_path = pass1_paths[0]

        # pass2 writes the same path with new content. Simulate the
        # post-deploy on-disk state (pass1's file now has pass2's bytes).
        pass2_content = b"pass2 content\n"
        target = Path("/" + shared_path)
        target.write_bytes(pass2_content)
        pass2_hash = _sha256_bytes(pass2_content)

        pass2_id = self.db.add_installed(
            name="supersede-pass2", version="2.0", install_method="archive",
        )
        # Atomic supersede mirroring the Phase 4 installer transaction:
        # add_files for new state, transfer_file_ownership for the
        # overlapping path with the new hash, mark_superseded.
        self.db.add_files(
            pass2_id, [shared_path], hashes={shared_path: pass2_hash},
        )
        self.db.transfer_file_ownership(
            "supersede-pass1", pass2_id, [shared_path],
            hashes={shared_path: pass2_hash},
        )
        self.db.mark_superseded("supersede-pass1", "supersede-pass2")
        self.db.conn.commit()
        return pass2_id, shared_path, pass2_content

    def test_superseded_package_routes_to_successor(self):
        self._install_pass1_pass2()
        result = self.verifier.verify("supersede-pass1", mode="strict")
        self.assertIsNotNone(result)
        self.assertEqual(result["superseded_by"], "supersede-pass2")
        self.assertEqual(result["exit_code"], EXIT_SUPERSEDED)
        self.assertIn("supersede-pass2", result["message"])
        self.assertIn("verify", result["message"].lower())

    def test_verify_all_skips_superseded(self):
        self._install_pass1_pass2()
        results = self.verifier.verify_all(mode="strict")
        names = {name for (name, _version, _result) in results}
        self.assertIn("supersede-pass2", names)
        self.assertNotIn("supersede-pass1", names)


class TestSurfaceContracts(_VerifierTestBase):
    """API-shape contracts: nonexistent package, default mode, invalid mode."""

    def test_verify_nonexistent_package_returns_none(self):
        self.assertIsNone(self.verifier.verify("does-not-exist"))
        self.assertIsNone(self.verifier.verify("does-not-exist", mode="fast"))

    def test_default_mode_is_strict(self):
        # Install a package, mutate its file, call verify() WITHOUT mode arg.
        # If default is strict, the mutation is caught.
        _, path_keys, _ = self._install_package(
            "default-mode-pkg", "1.0", {"data/default.txt": b"original\n"},
        )
        target = self.tempdir / "data" / "default.txt"
        target.write_bytes(b"mutated\n")
        result = self.verifier.verify("default-mode-pkg")
        self.assertIsNotNone(result)
        self.assertEqual(result["modified"], path_keys)
        self.assertEqual(result["exit_code"], EXIT_MODIFIED)

    def test_invalid_mode_falls_through_to_fast(self):
        # The mode comparison in verify() is `strict=(mode == "strict")`.
        # Anything other than the string "strict" — including "garbage"
        # or None — currently falls through to fast-mode semantics.
        # This documents the actual behavior so any future mode-validation
        # tightening is a deliberate choice rather than a quiet contract
        # change.
        _, _, _ = self._install_package(
            "invalid-mode-pkg", "1.0", {"data/invalid.txt": b"original\n"},
        )
        target = self.tempdir / "data" / "invalid.txt"
        target.write_bytes(b"mutated\n")
        result = self.verifier.verify("invalid-mode-pkg", mode="garbage")
        self.assertIsNotNone(result)
        # Falls through to fast: existence is OK, content mismatch ignored.
        self.assertEqual(result["modified"], [])
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["exit_code"], EXIT_OK)


if __name__ == "__main__":
    unittest.main()
