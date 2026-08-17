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
        # tempfile.mkdtemp gives an absolute path. The DB stores file
        # paths relative to its install root and `verify_package`
        # reconstructs the absolute path via `Path(self.root) / path`
        # (H-011 remediation). Pass root=tempdir so the verifier walks
        # the test tree rather than the host's filesystem — necessary
        # on Windows where lstripping the "/" off an absolute path
        # produces a still-absolute string (C:\... has no leading
        # slash to strip) that would otherwise confuse the legacy
        # `"/" + path` reconstruction pattern.
        self._tempdir = tempfile.mkdtemp(prefix="pkm-verifier-test-")
        self.tempdir = Path(self._tempdir)
        db_path = self.tempdir / "pkm.db"
        self.db = PackageDB(db_path=str(db_path), root=str(self.tempdir))
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

        DB-shape relative path is the path relative to PackageDB.root
        (i.e. relative to self.tempdir). The verifier reconstructs the
        absolute path via Path(self.root) / relative_path which lands
        on the same on-disk file cross-platform.
        """
        abs_path = self.tempdir / relative_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(content)
        return relative_path

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
        target = self.tempdir / shared_path
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


class TestVerifyExemptions(_VerifierTestBase):
    """PKM-E1/E2/E3: verify must not cry wolf on a clean install for
    legitimately multi-owned files (a `-pass2` bootstrap twin + its base),
    config files, or generated index files — while still catching real tampering.
    """

    @staticmethod
    def _sha(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def test_multi_owner_pass2_overlap_not_flagged(self):
        # PKM-E1 (Option 1): base pkg + its -pass2 twin both own the same file;
        # only the pass2 bytes are on disk, so the base's recorded hash is stale.
        path = "usr/lib/libfoo.so.1"
        base, pass2 = b"base build of libfoo\n", b"pass2 build of libfoo\n"
        base_id = self.db.add_installed(
            name="libfoo", version="1.0", install_method="archive")
        self.db.add_files(base_id, [path], hashes={path: self._sha(base)})
        # live file = the pass2 build; the pass2 package records that hash
        self._write_file(path, pass2)
        pass2_id = self.db.add_installed(
            name="libfoo-pass2", version="1.0", install_method="archive")
        self.db.add_files(pass2_id, [path], hashes={path: self._sha(pass2)})
        # base verify: live != base's recorded hash, but matches the pass2 owner -> OK
        self.assertEqual(
            self.verifier.verify("libfoo")["modified"], [],
            "a file matching another owning package's recorded hash must not be flagged")
        # pass2 verify: live == its own recorded hash -> OK
        self.assertEqual(self.verifier.verify("libfoo-pass2")["modified"], [])

    def test_tamper_matching_no_owner_is_flagged(self):
        # Same two-owner setup, but the live file matches NEITHER owner -> tamper.
        path = "usr/lib/libfoo.so.1"
        base, pass2, evil = b"base build\n", b"pass2 build\n", b"malicious payload\n"
        base_id = self.db.add_installed(
            name="libfoo", version="1.0", install_method="archive")
        self.db.add_files(base_id, [path], hashes={path: self._sha(base)})
        pass2_id = self.db.add_installed(
            name="libfoo-pass2", version="1.0", install_method="archive")
        self.db.add_files(pass2_id, [path], hashes={path: self._sha(pass2)})
        self._write_file(path, evil)
        self.assertIn(
            path, self.verifier.verify("libfoo")["modified"],
            "a file matching NO owning package's hash must still be flagged (tamper)")

    def test_config_file_content_change_not_flagged(self):
        # PKM-E3 (Class 2): /etc conffiles legitimately diverge after install.
        path = "etc/myapp.conf"
        pid = self.db.add_installed(
            name="myapp", version="1.0", install_method="archive")
        self._write_file(path, b"KEY=original\n")
        self.db.add_files(pid, [path], hashes={path: self._sha(b"KEY=original\n")})
        self._write_file(path, b"KEY=edited-by-admin\n")
        self.assertEqual(
            self.verifier.verify("myapp")["modified"], [],
            "a conffile content change must not be flagged modified")
        # ...but a MISSING conffile is still caught (existence is still checked).
        (self.tempdir / path).unlink()
        self.assertIn(path, self.verifier.verify("myapp")["missing"])

    def test_generated_info_dir_not_flagged(self):
        # PKM-E2 (Class 1): the GNU info dir is rewritten by install-info.
        path = "usr/share/info/dir"
        pid = self.db.add_installed(
            name="texinfo", version="1.0", install_method="archive")
        self._write_file(path, b"info dir v1\n")
        self.db.add_files(pid, [path], hashes={path: self._sha(b"info dir v1\n")})
        self._write_file(path, b"info dir v2 with more entries\n")
        self.assertEqual(self.verifier.verify("texinfo")["modified"], [])

    def test_reconcile_rerecords_post_install_mutation(self):
        # PKM-E: a file mutated AFTER its hash was recorded (MOK-sign / hook edit)
        # -> reconcile re-records the live hash so verify validates the real bytes.
        path = "usr/bin/somebin"
        pid = self.db.add_installed(
            name="somepkg", version="1.0", install_method="archive")
        self._write_file(path, b"unsigned build\n")
        self.db.add_files(pid, [path], hashes={path: self._sha(b"unsigned build\n")})
        # a post-install step rewrites the file (e.g. sbsign appends a PE signature)
        self._write_file(path, b"signed build with PE signature\n")
        # pre-reconcile: verify flags it (live != recorded, no other owner)
        self.assertIn(path, self.verifier.verify("somepkg")["modified"])
        # reconcile re-records from the live filesystem
        self.assertGreaterEqual(self.db.reconcile_checksums_from_live(), 1)
        # post-reconcile: clean (recorded == live)
        self.assertEqual(self.verifier.verify("somepkg")["modified"], [])
        # a SUBSEQUENT genuine tamper is still caught (baseline is the install state)
        self._write_file(path, b"malware\n")
        self.assertIn(path, self.verifier.verify("somepkg")["modified"])

    def test_reconcile_leaves_config_and_absent_files(self):
        # reconcile must not touch config files (content-exempt) or absent files.
        cfg, gone = "etc/x.conf", "usr/lib/absent.so"
        pid = self.db.add_installed(
            name="cfgpkg", version="1.0", install_method="archive")
        self._write_file(cfg, b"orig\n")  # 'gone' is recorded but never written (stripped)
        self.db.add_files(
            pid, [cfg, gone],
            hashes={cfg: self._sha(b"orig\n"), gone: self._sha(b"unshipped\n")})
        self.db.reconcile_checksums_from_live()
        # config row checksum unchanged (reconcile skips is_config)
        row = self.db.conn.execute(
            "SELECT checksum FROM files WHERE path = ?", (cfg,)).fetchone()
        self.assertEqual(row[0], self._sha(b"orig\n"))
        # absent file's row untouched -> still flagged missing by verify
        self.assertIn(gone, self.verifier.verify("cfgpkg")["missing"])


if __name__ == "__main__":
    unittest.main()
