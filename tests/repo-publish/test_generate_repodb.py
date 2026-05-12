"""Tests for E1.B.6 generate-repodb.py — index generator + signer."""

import gzip
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "generate-repodb.py"

# Load module by file path (scripts/ has no __init__.py)
spec = importlib.util.spec_from_file_location("generate_repodb", SCRIPT_PATH)
_grm = importlib.util.module_from_spec(spec)
sys.modules["generate_repodb"] = _grm
spec.loader.exec_module(_grm)

_load_release_keys = _grm._load_release_keys


class TestLoadReleaseKeys:
    def test_config_file_exists(self):
        keys = _load_release_keys()
        assert keys
        assert "S1" in keys
        assert "S2" in keys
        assert "NK1" in keys
        assert "NK2" in keys

    def test_fingerprints_are_40_chars(self):
        keys = _load_release_keys()
        for name, fp in keys.items():
            assert len(fp) == 40, f"{name} fingerprint is {len(fp)} chars, expected 40"
            assert all(c in "0123456789ABCDEF" for c in fp), f"{name} fingerprint contains non-hex chars"

    def test_aliases_match_canonical(self):
        keys = _load_release_keys()
        assert keys["NK1"] == keys["S1"], "NK1 alias must match S1"
        assert keys["NK2"] == keys["S2"], "NK2 alias must match S2"

    def test_s1_matches_canonical(self):
        canonical = "D7AA641D81ACD690C5AD865E7276E14DD8886BFE"
        keys = _load_release_keys()
        assert keys["S1"] == canonical


class TestGenerateRepodbSmoke:
    def test_help_exits_zero(self):
        result = subprocess.run(
            [sys.executable, "scripts/generate-repodb.py", "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_no_sign_generates_index(self, tmp_path):
        """Smoke test: generate index from mock archives (no signing)."""
        # Create dummy archives
        (tmp_path / "testpkg-1.0.igos.tar.gz").write_bytes(b"fake-archive-content")
        (tmp_path / "testpkg2-2.0.igos.tar.gz").write_bytes(b"fake-archive-content-2")

        output = tmp_path / "InterGenOS.db"
        result = subprocess.run(
            [
                sys.executable, "scripts/generate-repodb.py",
                "--no-sign", "-o", str(output), str(tmp_path),
            ],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert output.exists()
        assert output.stat().st_size > 0


class TestIndexRoundtrip:
    def test_pkm_parser_roundtrip(self, tmp_path):
        """Index produced by generate-repodb.py must roundtrip through pkm.repo.RepoIndex."""
        # Create a valid .igos.tar.gz with .PKGINFO inside
        import tarfile as tarfile_mod
        archive = tmp_path / "testpkg-1.0.igos.tar.gz"
        with tarfile_mod.open(archive, "w:gz") as tar:
            # .PKGINFO with metadata
            pkginfo = (
                "pkgname = testpkg\n"
                "pkgver = 1.0-1\n"
                "pkgdesc = Test package for roundtrip\n"
                "depend = glibc\n"
                "license = MIT\n"
                "tier = core\n"
                "builddate = 2026-05-12T00:00:00Z\n"
                "size = 1000\n"
            )
            import io
            info = tarfile_mod.TarInfo(name=".PKGINFO")
            info.size = len(pkginfo)
            tar.addfile(info, io.BytesIO(pkginfo.encode()))

        output = tmp_path / "InterGenOS.db"
        result = subprocess.run(
            [
                sys.executable, str(_PROJECT_ROOT / "scripts" / "generate-repodb.py"),
                "--no-sign", "-o", str(output), str(tmp_path),
            ],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr

        # Parse with pkm.repo
        sys.path.insert(0, str(_PROJECT_ROOT))
        from pkm.repo import RepoIndex

        with gzip.open(output, "rt", encoding="utf-8") as f:
            data = json.load(f)

        index = RepoIndex("test", "https://example.com/x86_64", data)
        assert index.version == 1
        assert index.arch == "x86_64"
        assert index.package_count == 1
        assert "testpkg" in index.packages

        pkg = index.packages["testpkg"]
        assert "sha256" in pkg
        assert "filename" in pkg
        assert pkg["filename"] == "testpkg-1.0.igos.tar.gz"

    def test_missing_packages_key_does_not_crash(self, tmp_path):
        """RepoIndex handles empty or missing packages gracefully."""
        index_data = {"version": 1, "generated": "2026-01-01T00:00:00Z", "arch": "x86_64", "package_count": 0}
        from pkm.repo import RepoIndex
        index = RepoIndex("test", "https://example.com/x86_64", index_data)
        assert index.package_count == 0

    def test_required_top_level_fields(self, tmp_path):
        import tarfile as tarfile_mod, io
        archive = tmp_path / "dummy-1.0.igos.tar.gz"
        with tarfile_mod.open(archive, "w:gz") as tar:
            pkginfo = "pkgname = dummy\npkgver = 1.0-1\n"
            info = tarfile_mod.TarInfo(name=".PKGINFO")
            info.size = len(pkginfo)
            tar.addfile(info, io.BytesIO(pkginfo.encode()))

        output = tmp_path / "InterGenOS.db"
        subprocess.run(
            [sys.executable, str(_PROJECT_ROOT / "scripts" / "generate-repodb.py"),
             "--no-sign", "-o", str(output), str(tmp_path)],
            capture_output=True,
        )

        with gzip.open(output, "rt", encoding="utf-8") as f:
            data = json.load(f)

        assert "version" in data
        assert "generated" in data
        assert "arch" in data
        assert "package_count" in data
        assert "packages" in data
        assert data["arch"] == "x86_64"
        assert data["package_count"] >= 0


class TestGpgSignRoundtrip:
    def test_sign_then_verify(self, tmp_path):
        """If GPG is available, sign the index and verify the signature."""
        # Check if GPG is available (skip test gracefully if not)
        gpg_check = subprocess.run(["gpg", "--version"], capture_output=True)
        if gpg_check.returncode != 0:
            pytest.skip("GPG not available")

        # Create a throwaway key to test with (no external keyring pollution)
        key_id = "test-key-for-repodb-tests"
        subprocess.run(
            ["gpg", "--batch", "--passphrase", "", "--quick-gen-key", key_id],
            capture_output=True,
        )
        assert key_id in subprocess.run(
            ["gpg", "--list-keys", key_id], capture_output=True, text=True
        ).stdout

        # Create archive and index
        (tmp_path / "testpkg-1.0.igos.tar.gz").write_bytes(b"content")
        output = tmp_path / "InterGenOS.db"
        subprocess.run(
            [sys.executable, "scripts/generate-repodb.py", "--no-sign", "-o", str(output), str(tmp_path)],
            capture_output=True,
        )

        # Sign using the throwaway key
        sig_path = Path(str(output) + ".sig")
        result = subprocess.run(
            ["gpg", "--detach-sign", "--armor", "--local-user", key_id,
             "--output", str(sig_path), str(output)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert sig_path.exists()

        # Verify signature
        result = subprocess.run(
            ["gpg", "--verify", str(sig_path), str(output)],
            capture_output=True, text=True,
        )
        assert "Good signature" in result.stderr or result.returncode == 0, result.stderr

        # Cleanup throwaway key
        subprocess.run(["gpg", "--batch", "--yes", "--delete-secret-and-public-key", key_id], capture_output=True)
