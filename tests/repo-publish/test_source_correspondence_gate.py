"""The publish refuses a binary archive without its corresponding source.

scripts/check-source-correspondence.py is the fail-closed gate publish-repo.sh
fires in preflight (decided 2026-08-04): every staged binary archive must have
its matching `<name>-<version>-<release>.igos.src.tar.gz`, or its recipe must
declare no upstream source — the pure-data class, DERIVED from the packages
tree at gate time and printed name by name, never a hidden allowlist. What is
pinned here:

  1. full correspondence passes, and the pass line accounts for every archive;
  2. a source-declaring package with no source archive is a refusal that names
     the exact missing filename;
  3. a pure-data package (source: []) is exempt and its name is printed;
  4. a staged binary with no recipe in the tree fails closed — its source-less
     status cannot be proven;
  5. a binary whose .PKGINFO cannot be read fails closed — unprovable identity;
  6. identity comes from .PKGINFO, not the filename: a misnamed archive file
     still resolves to its real package.

The gate is exercised exactly as publish-repo.sh runs it: as a subprocess,
judged on exit code and output.
"""

import io
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
GATE = _PROJECT_ROOT / "scripts" / "check-source-correspondence.py"


def make_binary_archive(path: Path, name: str, ver: str, rel: str,
                        with_pkginfo: bool = True) -> None:
    """Write a minimal .igos.tar.gz whose identity lives in ./.PKGINFO."""
    with tarfile.open(path, "w:gz") as t:
        if with_pkginfo:
            payload = f"pkgname = {name}\npkgver = {ver}\npkgrel = {rel}\n".encode()
            info = tarfile.TarInfo("./.PKGINFO")
            info.size = len(payload)
            t.addfile(info, io.BytesIO(payload))
        filler = b"BINARY-PAYLOAD"
        info = tarfile.TarInfo("./usr/share/demo/file")
        info.size = len(filler)
        t.addfile(info, io.BytesIO(filler))


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        packages = root / "packages"
        # A source-declaring recipe and a pure-data recipe.
        srcful = packages / "core" / "openssl"
        srcful.mkdir(parents=True)
        (srcful / "package.yml").write_text(
            "name: openssl\nversion: \"3.6.0\"\nrelease: 2\n"
            "source:\n  - url: https://example.invalid/openssl-3.6.0.tar.gz\n"
            "    sha256: " + "0" * 64 + "\n")
        pure = packages / "core" / "intergenos-keyring"
        pure.mkdir(parents=True)
        (pure / "package.yml").write_text(
            "name: intergenos-keyring\nversion: \"1.0\"\nrelease: 3\nsource: []\n")

        archives = root / "archives"
        archives.mkdir()
        src_archives = root / "sources-archives"
        src_archives.mkdir()
        yield root, packages, archives, src_archives


def run_gate(root: Path, packages: Path, archives: Path, src_archives: Path):
    return subprocess.run(
        [sys.executable, str(GATE),
         "--archive-dir", str(archives),
         "--sources-archive-dir", str(src_archives),
         "--packages-root", str(packages)],
        capture_output=True, text=True)


class TestFullCorrespondencePasses:
    def test_matched_plus_exempt_exits_zero(self, workspace):
        root, packages, archives, src_archives = workspace
        make_binary_archive(archives / "openssl-3.6.0-2.igos.tar.gz",
                            "openssl", "3.6.0", "2")
        (src_archives / "openssl-3.6.0-2.igos.src.tar.gz").write_bytes(b"SRC")
        make_binary_archive(archives / "intergenos-keyring-1.0-3.igos.tar.gz",
                            "intergenos-keyring", "1.0", "3")
        r = run_gate(root, packages, archives, src_archives)
        assert r.returncode == 0, r.stderr
        assert "every one accounted for" in r.stdout

    def test_the_exempt_class_is_printed_by_name(self, workspace):
        root, packages, archives, src_archives = workspace
        make_binary_archive(archives / "intergenos-keyring-1.0-3.igos.tar.gz",
                            "intergenos-keyring", "1.0", "3")
        r = run_gate(root, packages, archives, src_archives)
        assert r.returncode == 0, r.stderr
        assert "source-less class" in r.stdout
        assert "intergenos-keyring-1.0-3" in r.stdout


class TestShortfallsRefuse:
    def test_a_missing_source_archive_is_named_exactly(self, workspace):
        root, packages, archives, src_archives = workspace
        make_binary_archive(archives / "openssl-3.6.0-2.igos.tar.gz",
                            "openssl", "3.6.0", "2")
        r = run_gate(root, packages, archives, src_archives)
        assert r.returncode == 1
        assert "openssl-3.6.0-2.igos.src.tar.gz" in r.stderr

    def test_every_shortfall_is_named_not_only_the_first(self, workspace):
        root, packages, archives, src_archives = workspace
        second = packages / "core" / "zlib"
        second.mkdir(parents=True)
        (second / "package.yml").write_text(
            "name: zlib\nversion: \"1.3.2\"\nrelease: 1\n"
            "source:\n  - url: https://example.invalid/zlib-1.3.2.tar.gz\n"
            "    sha256: " + "1" * 64 + "\n")
        make_binary_archive(archives / "openssl-3.6.0-2.igos.tar.gz",
                            "openssl", "3.6.0", "2")
        make_binary_archive(archives / "zlib-1.3.2-1.igos.tar.gz",
                            "zlib", "1.3.2", "1")
        r = run_gate(root, packages, archives, src_archives)
        assert r.returncode == 1
        assert "openssl-3.6.0-2.igos.src.tar.gz" in r.stderr
        assert "zlib-1.3.2-1.igos.src.tar.gz" in r.stderr

    def test_a_binary_with_no_recipe_fails_closed(self, workspace):
        root, packages, archives, src_archives = workspace
        make_binary_archive(archives / "mystery-9.9-1.igos.tar.gz",
                            "mystery", "9.9", "1")
        r = run_gate(root, packages, archives, src_archives)
        assert r.returncode == 1
        assert "mystery" in r.stderr
        assert "cannot be proven" in r.stderr

    def test_an_unreadable_pkginfo_fails_closed(self, workspace):
        root, packages, archives, src_archives = workspace
        make_binary_archive(archives / "broken-1.0-1.igos.tar.gz",
                            "broken", "1.0", "1", with_pkginfo=False)
        r = run_gate(root, packages, archives, src_archives)
        assert r.returncode == 1
        assert "broken-1.0-1.igos.tar.gz" in r.stderr
        assert "unprovable" in r.stderr or "unreadable" in r.stderr


class TestIdentityComesFromPkginfo:
    def test_a_misnamed_archive_file_resolves_to_its_real_package(self, workspace):
        # The file on disk is misleadingly named; .PKGINFO says openssl. The
        # gate must look for openssl's source archive, and pass when it exists.
        root, packages, archives, src_archives = workspace
        make_binary_archive(archives / "totally-wrong-name.igos.tar.gz",
                            "openssl", "3.6.0", "2")
        (src_archives / "openssl-3.6.0-2.igos.src.tar.gz").write_bytes(b"SRC")
        r = run_gate(root, packages, archives, src_archives)
        assert r.returncode == 0, r.stderr


class TestEmptyStagingRefuses:
    def test_no_staged_binaries_is_an_error_not_a_pass(self, workspace):
        root, packages, archives, src_archives = workspace
        r = run_gate(root, packages, archives, src_archives)
        assert r.returncode == 2
        assert "no *.igos.tar.gz" in r.stderr


def make_twin_source_archive(path: Path, twin: str, ver: str, rel: str,
                             tarball: str, payload: bytes) -> None:
    """Write a twin source archive in the emitted layout:
    <twin>-<ver>-<rel>/<tarball> holding the upstream bytes."""
    top = f"{twin}-{ver}-{rel}"
    with tarfile.open(path, "w:gz") as t:
        info = tarfile.TarInfo(f"{top}/{tarball}")
        info.size = len(payload)
        t.addfile(info, io.BytesIO(payload))


class TestRecipelessMinimalCoreTwins:
    """A recipe-less Chapter-8 binary is accepted ONLY on a hash-proven
    toolchain twin (decided 2026-08-04): same-version <name>-pass*/<name>-tmp
    recipe, pinned primary source, twin archive staged, and the bundled
    tarball hashing to the recipe pin — verified by the gate itself."""

    PAYLOAD = b"UPSTREAM-TARBALL-BYTES"

    def _add_twin(self, packages: Path, src_archives: Path,
                  twin: str = "binutils-pass1", ver: str = "2.46.0",
                  payload: bytes = PAYLOAD, recipe_ver: str | None = None):
        import hashlib
        pin = hashlib.sha256(self.PAYLOAD).hexdigest()
        d = packages / "toolchain" / twin
        d.mkdir(parents=True)
        (d / "package.yml").write_text(
            f"name: {twin}\nversion: \"{recipe_ver or ver}\"\nrelease: 1\n"
            f"source:\n"
            f"  - url: https://example.invalid/binutils-${{version}}.tar.xz\n"
            f"    sha256: {pin}\n")
        make_twin_source_archive(
            src_archives / f"{twin}-{recipe_ver or ver}-1.igos.src.tar.gz",
            twin, recipe_ver or ver, "1",
            f"binutils-{recipe_ver or ver}.tar.xz", payload)

    def test_a_hash_proven_twin_is_accepted_and_printed(self, workspace):
        root, packages, archives, src_archives = workspace
        make_binary_archive(archives / "binutils-2.46.0-1.igos.tar.gz",
                            "binutils", "2.46.0", "1")
        self._add_twin(packages, src_archives)
        r = run_gate(root, packages, archives, src_archives)
        assert r.returncode == 0, r.stderr
        assert "recipe-less minimal-core class" in r.stdout
        assert "binutils-pass1-2.46.0-1.igos.src.tar.gz" in r.stdout
        assert "sha256 verified" in r.stdout

    def test_a_bundled_tarball_that_mismatches_the_pin_refuses(self, workspace):
        root, packages, archives, src_archives = workspace
        make_binary_archive(archives / "binutils-2.46.0-1.igos.tar.gz",
                            "binutils", "2.46.0", "1")
        # Recipe pins the real payload's hash; the staged twin bundles
        # DIFFERENT bytes — presence without hash proof must refuse.
        self._add_twin(packages, src_archives, payload=b"TAMPERED-BYTES")
        r = run_gate(root, packages, archives, src_archives)
        assert r.returncode == 1
        assert "cannot be proven" in r.stderr

    def test_a_version_mismatched_twin_refuses(self, workspace):
        root, packages, archives, src_archives = workspace
        make_binary_archive(archives / "binutils-2.46.0-1.igos.tar.gz",
                            "binutils", "2.46.0", "1")
        self._add_twin(packages, src_archives, recipe_ver="2.45.0")
        r = run_gate(root, packages, archives, src_archives)
        assert r.returncode == 1
        assert "cannot be proven" in r.stderr

    def test_a_twin_recipe_without_a_staged_archive_refuses(self, workspace):
        root, packages, archives, src_archives = workspace
        make_binary_archive(archives / "binutils-2.46.0-1.igos.tar.gz",
                            "binutils", "2.46.0", "1")
        self._add_twin(packages, src_archives)
        (src_archives / "binutils-pass1-2.46.0-1.igos.src.tar.gz").unlink()
        r = run_gate(root, packages, archives, src_archives)
        assert r.returncode == 1
        assert "cannot be proven" in r.stderr

    def test_a_recipe_named_like_a_twin_of_nothing_stays_refused(self, workspace):
        # The original no-recipe refusal is unchanged for names with no twin.
        root, packages, archives, src_archives = workspace
        make_binary_archive(archives / "mystery-9.9-1.igos.tar.gz",
                            "mystery", "9.9", "1")
        r = run_gate(root, packages, archives, src_archives)
        assert r.returncode == 1
        assert "cannot be proven" in r.stderr
