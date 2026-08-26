"""Tests for E1.B.5 emit-package-archives.py — archive emitter from chroot manifests."""

import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tarfile
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "emit-package-archives.py"

# Load module by file path (scripts/ has no __init__.py)
spec = importlib.util.spec_from_file_location("emit_package_archives", SCRIPT_PATH)
_epam = importlib.util.module_from_spec(spec)
sys.modules["emit_package_archives"] = _epam
spec.loader.exec_module(_epam)

_read_manifest = _epam._read_manifest
_read_pkg_yml_fields = _epam._read_pkg_yml_fields
_resolve_package_yml = _epam._resolve_package_yml
_resolve_root = _epam._resolve_root
_sha256_file = _epam._sha256_file
emit_archive = _epam.emit_archive


class TestResolveRoot:
    def test_autodetect_from_script_location(self):
        root = _resolve_root()
        assert root.is_dir()
        assert (root / "scripts").is_dir()
        assert (root / "packages").is_dir()

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("INTERGENOS_ROOT", "/tmp/fake-root")
        root = _resolve_root()
        assert str(root) == "/tmp/fake-root"


class TestResolvePackageYml:
    def test_finds_package_in_desktop_tier(self):
        """Find a package in ANY tier (gimp moved between tiers over time)."""
        path = _resolve_package_yml("gimp")
        assert path is not None
        assert path.exists()
        assert "packages/" in str(path) and str(path).endswith("gimp/package.yml")

    def test_finds_package_in_core_tier(self):
        """A core-tier recipe resolves by its own directory name.

        This case named "glibc" until 2026-08-25 and passed on
        packages/toolchain/glibc — a TOOLCHAIN recipe, so it proved the
        opposite of what its name claims. The core-tier recipe has always
        been glibc-core.
        """
        path = _resolve_package_yml("glibc-core")
        assert path is not None
        assert path.exists()
        assert str(path).endswith("packages/core/glibc-core/package.yml")

    def test_a_ship_name_resolves_to_the_recipe_that_declares_it(self):
        """A manifest carries the name a package SHIPS under, which for the
        Chapter-8 dual-name packages is not any recipe directory's name.

        Resolving by directory alone gave the wrong answer twice over. Before
        2026-08-25 `glibc` found packages/toolchain/glibc, whose tier and
        release describe the cross build rather than the archive being
        emitted — the same class gen-pkginfo.py's find_ships_as_recipe()
        closed on 2026-07-30. After those three recipes were renamed to -tmp
        it found nothing at all, and the emitted .PKGINFO would have carried
        no tier, license, release or description. The recipe that DECLARES
        the ship name is the right answer in both states.
        """
        for ship, recipe in (("glibc", "core/glibc-core"),
                             ("m4", "core/m4-core"),
                             ("ncurses", "core/ncurses-core")):
            path = _resolve_package_yml(ship)
            assert path is not None, f"{ship} resolved to nothing"
            assert str(path).endswith(f"packages/{recipe}/package.yml"), \
                f"{ship} resolved to {path}"

    def test_a_ship_name_match_is_checked_against_the_version(self):
        """The ships_as match is accepted only at the recipe's own version,
        the same checked contract gen-pkginfo.py applies. With a version that
        matches nothing, the declarer is not accepted and no directory of that
        name exists, so the answer is None rather than a wrong recipe."""
        assert _resolve_package_yml("glibc", version="2.43") is not None
        assert _resolve_package_yml("glibc", version="0.0.0") is None

    def test_a_recipe_directory_still_resolves_by_its_own_name(self):
        """The directory search is unchanged for every recipe that is named
        what its manifest is called."""
        path = _resolve_package_yml("glibc-tmp")
        assert path is not None
        assert str(path).endswith("packages/toolchain/glibc-tmp/package.yml")

    def test_finds_package_in_toolchain_tier(self):
        """Item 2 fix: toolchain tier must be searchable."""
        path = _resolve_package_yml("binutils-pass1")
        assert path is not None
        assert path.exists()

    def test_returns_none_for_missing_package(self):
        path = _resolve_package_yml("no-such-package-xyzzy")
        assert path is None

    def test_no_dead_config_tier(self):
        """Item 3 fix: packages/config/ should not exist or be referenced."""
        config_dir = Path("packages/config")
        assert not config_dir.exists() or not config_dir.is_dir()

class TestReadPkgYmlFields:
    """The fields this parser lifts are stamped verbatim into the archive's
    own .PKGINFO, which pkm reads at install time (pkm/repo.py:575)."""

    def test_a_trailing_note_is_not_part_of_the_field(self):
        """packages/core/glibc-core carries `release: 4  # r4: ...` — a note
        of several sentences. A `#` after whitespace opens a YAML comment, so
        the value is 4; without that rule the whole note is stamped into
        pkgrel. Measured 2026-08-25: 294 recipes carry such a note on one of
        the fields read here, and none of the 7063 values themselves contains
        a space followed by a hash.
        """
        yml = _PROJECT_ROOT / "packages" / "core" / "glibc-core" / "package.yml"
        fields = _read_pkg_yml_fields(yml)
        assert fields["release"] == "4"
        assert fields["ships_as"] == "glibc"
        assert fields["description"] == "GNU C Library (final system)"

    def test_a_quoted_value_keeps_its_content(self):
        yml = _PROJECT_ROOT / "packages" / "core" / "m4-core" / "package.yml"
        fields = _read_pkg_yml_fields(yml)
        assert fields["version"] == "1.4.21"
        assert fields["release"] == "1"
        assert fields["tier"] == "core"

    def test_a_value_with_no_note_is_untouched(self):
        yml = _PROJECT_ROOT / "packages" / "toolchain" / "glibc-tmp" / "package.yml"
        fields = _read_pkg_yml_fields(yml)
        assert fields["name"] == "glibc-tmp"
        assert fields["version"] == "2.43"
        assert fields["tier"] == "toolchain"


class TestReadManifest:
    def test_parse_slackware_manifest(self, tmp_path):
        manifest = tmp_path / "firefox-138.0"
        manifest.write_text(
            "PACKAGE NAME: firefox-138.0\n"
            "PACKAGE VERSION: 138.0\n"
            "UNCOMPRESSED SIZE: 215M (215000000 bytes)\n"
            "BUILD DATE: 2026-05-11T12:00:00Z\n"
            "BUILD SYSTEM: InterGenOS LFS 13.0\n"
            "DESCRIPTION:\n"
            "firefox: Mozilla Firefox web browser\n"
            "\n"
            "FILE LIST:\n"
            "usr/bin/firefox\n"
            "usr/lib/firefox/firefox\n"
            "usr/share/applications/firefox.desktop\n"
        )
        meta = _read_manifest(manifest)
        assert meta["name"] == "firefox"
        assert meta["full_name"] == "firefox-138.0"
        assert meta["version"] == "138.0"
        assert meta["installed_size"] == 215000000
        assert meta["build_date"] == "2026-05-11T12:00:00Z"
        assert meta["description"] == "firefox: Mozilla Firefox web browser"
        assert "usr/bin/firefox" in meta["files"]
        assert "usr/lib/firefox/firefox" in meta["files"]
        assert "usr/share/applications/firefox.desktop" in meta["files"]


class TestSha256File:
    def test_sha256_deterministic(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello, intergenos!")
        sha1 = _sha256_file(f)
        sha2 = _sha256_file(f)
        assert sha1 == sha2
        assert len(sha1) == 64

    def test_sha256_different_content(self, tmp_path):
        f1 = tmp_path / "a.bin"
        f1.write_bytes(b"aaa")
        f2 = tmp_path / "b.bin"
        f2.write_bytes(b"bbb")
        assert _sha256_file(f1) != _sha256_file(f2)


class TestEmitArchive:
    def test_smoke_emit_single_package(self, tmp_path):
        """Smoke test: create a mock chroot with one installed file + manifest."""
        chroot = tmp_path / "chroot"
        chroot.mkdir()
        (chroot / "usr/bin").mkdir(parents=True)
        (chroot / "usr/bin/hello").write_text("#!/bin/sh\necho hello\n")

        manifests = tmp_path / "manifests"
        manifests.mkdir()
        manifest = manifests / "hello-1.0"
        manifest.write_text(
            "PACKAGE NAME: hello-1.0\n"
            "PACKAGE VERSION: 1.0\n"
            "UNCOMPRESSED SIZE: 1K (1000 bytes)\n"
            "BUILD DATE: 2026-05-12T00:00:00Z\n"
            "BUILD SYSTEM: InterGenOS LFS 13.0\n"
            "DESCRIPTION:\n"
            "hello: Test package\n"
            "\n"
            "FILE LIST:\n"
            "usr/bin/hello\n"
        )

        output = tmp_path / "output"
        output.mkdir()

        result = emit_archive(manifest, chroot, output)
        assert result is not None
        assert result["name"] == "hello"
        assert result["version"] == "1.0"
        assert result["filename"] == "hello-1.0.igos.tar.gz"
        assert len(result["sha256"]) == 64
        assert result["files"] == 1

        archive = output / "hello-1.0.igos.tar.gz"
        assert archive.exists()
        assert archive.stat().st_size > 0

        # Verify archive contents
        with tarfile.open(archive, "r:gz") as tar:
            names = tar.getnames()
            assert "usr/bin/hello" in names
            assert ".PKGINFO" in names

        # Verify sha256 matches
        actual_sha = _sha256_file(archive)
        assert actual_sha == result["sha256"]

    def test_skips_missing_files(self, tmp_path):
        """Manifest with files not in chroot should still produce archive."""
        chroot = tmp_path / "chroot"
        chroot.mkdir()

        manifests = tmp_path / "manifests"
        manifests.mkdir()
        manifest = manifests / "empty-1.0"
        manifest.write_text(
            "PACKAGE NAME: empty-1.0\n"
            "PACKAGE VERSION: 1.0\n"
            "DESCRIPTION:\n"
            "FILE LIST:\n"
            "usr/bin/nonexistent\n"
        )

        output = tmp_path / "output"
        output.mkdir()

        result = emit_archive(manifest, chroot, output)
        # Should be None because no files found (filtered to empty)
        assert result is None
