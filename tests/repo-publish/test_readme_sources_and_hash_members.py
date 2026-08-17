"""The source archive carries the two members SOURCES.md §3 promises.

SOURCES.md §3 lists `<upstream-tarball>.sha256` and `README.SOURCES` as members
of every corresponding-source archive; until this change the generator emitted
neither, so every published archive was short two promised members. What is
pinned here:

  1. every bundled tarball gets a `<tarball>.sha256` member whose content is
     sha256sum -c compatible and whose value matches the staged bytes;
  2. the emitted value is VERIFIED: a pinned entry whose staged bytes hash
     differently is refused with both hashes named — a hash file that
     contradicts the bytes beside it would be worse than no hash file;
  3. a pin-less entry (`generated: true` first-party tarballs carry no pin)
     still gets a hash file, carrying the computed value;
  4. README.SOURCES is present and lists the archive's ACTUAL members and the
     package's own reproduction command;
  5. the archive's top directory is `<name>-<version>-<release>/` exactly as
     §3 draws it (the prior `-src` suffix was drift from the documented
     layout);
  6. the bytes stay reproducible with the new members in place.

The assertions read the emitted archive, not the generator's internals.
"""

import hashlib
import importlib.util
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "build-source-archives.py"

spec = importlib.util.spec_from_file_location("build_source_archives_rm", SCRIPT_PATH)
_bsa = importlib.util.module_from_spec(spec)
sys.modules["build_source_archives_rm"] = _bsa
spec.loader.exec_module(_bsa)

TARBALL = ("openssl-3.6.0.tar.gz", b"UPSTREAM-TLS-SOURCE")
SECOND = ("openssl-fips-3.6.0.tar.gz", b"UPSTREAM-FIPS-SOURCE")


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        pkg_dir = root / "packages" / "core" / "openssl"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "build.sh").write_text("#!/bin/bash\n:\n")
        (pkg_dir / "package.yml").write_text("name: openssl\n")
        sources = root / "sources"
        sources.mkdir()
        for fname, payload in (TARBALL, SECOND):
            (sources / fname).write_bytes(payload)
        out = root / "out"
        out.mkdir()
        yield root, pkg_dir, sources, out


def _meta(entries=None):
    return {
        "name": "openssl",
        "version": "3.6.0",
        "release": 2,
        "source": entries if entries is not None else [
            {"url": f"https://example.invalid/{TARBALL[0]}",
             "sha256": _sha(TARBALL[1])},
        ],
    }


def _emit(workspace, meta, expect="ok"):
    root, pkg_dir, sources, out = workspace
    status, msg = _bsa.build_source_archive(pkg_dir, meta, sources, out, root)
    assert status == expect, f"expected {expect}, got {status}: {msg}"
    return msg


def _read(workspace, archive_name="openssl-3.6.0-2.igos.src.tar.gz"):
    _, _, _, out = workspace
    archive = out / archive_name
    assert archive.is_file(), f"no archive at {archive}"
    with tarfile.open(archive, "r:gz") as tf:
        full_names = tf.getnames()
        payloads = {
            Path(m.name).name: tf.extractfile(m).read()
            for m in tf.getmembers() if m.isfile()
        }
    return full_names, payloads, archive


class TestHashFileMembers:
    def test_every_tarball_gets_a_sha256_member(self, workspace):
        _emit(workspace, _meta([
            {"url": f"https://example.invalid/{TARBALL[0]}",
             "sha256": _sha(TARBALL[1])},
            {"url": f"https://example.invalid/{SECOND[0]}",
             "sha256": _sha(SECOND[1])},
        ]))
        _, payloads, _ = _read(workspace)
        for fname, payload in (TARBALL, SECOND):
            member = f"{fname}.sha256"
            assert member in payloads, f"{member} missing"
            assert payloads[member].decode() == f"{_sha(payload)}  {fname}\n"

    def test_the_hash_file_is_sha256sum_check_compatible(self, workspace):
        # Format contract: "<64 hex>  <filename>\n" — exactly what
        # `sha256sum -c` consumes. Checked structurally, not by running the
        # tool, so the test does not depend on coreutils.
        _emit(workspace, _meta())
        _, payloads, _ = _read(workspace)
        content = payloads[f"{TARBALL[0]}.sha256"].decode()
        digest, sep, rest = content.partition("  ")
        assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
        assert sep == "  "
        assert rest == f"{TARBALL[0]}\n"

    def test_a_pin_mismatch_is_refused_naming_both_hashes(self, workspace):
        root, pkg_dir, sources, out = workspace
        wrong_pin = "f" * 64
        msg = _emit(workspace, _meta([
            {"url": f"https://example.invalid/{TARBALL[0]}", "sha256": wrong_pin},
        ]), expect="fail")
        assert wrong_pin in msg
        assert _sha(TARBALL[1]) in msg
        assert not list(out.iterdir()), "no archive may be written on a refusal"

    def test_a_pinless_entry_gets_the_computed_hash(self, workspace):
        # generated: true first-party tarballs carry no sha256 pin; the archive
        # must still be self-verifying.
        _emit(workspace, _meta([
            {"url": f"https://example.invalid/{TARBALL[0]}"},
        ]))
        _, payloads, _ = _read(workspace)
        assert payloads[f"{TARBALL[0]}.sha256"].decode() == \
            f"{_sha(TARBALL[1])}  {TARBALL[0]}\n"


class TestReadmeSources:
    def test_the_member_is_present_and_names_the_package(self, workspace):
        _emit(workspace, _meta())
        _, payloads, _ = _read(workspace)
        assert "README.SOURCES" in payloads
        text = payloads["README.SOURCES"].decode()
        assert "openssl 3.6.0-2" in text
        assert "openssl-3.6.0-2.igos.tar.gz" in text

    def test_contents_lists_the_actual_members(self, workspace):
        _emit(workspace, _meta())
        _, payloads, _ = _read(workspace)
        text = payloads["README.SOURCES"].decode()
        assert TARBALL[0] in text
        assert f"{TARBALL[0]}.sha256" in text
        assert "build.sh" in text
        assert "package.yml" in text
        # No withheld input in this archive — the member is absent and the
        # README must not claim otherwise.
        assert "NON-REDISTRIBUTABLE.txt" not in text

    def test_the_reproduction_command_names_the_package(self, workspace):
        _emit(workspace, _meta())
        _, payloads, _ = _read(workspace)
        assert "--only openssl" in payloads["README.SOURCES"].decode()

    def test_a_withheld_input_is_named_in_contents(self, workspace):
        root, pkg_dir, sources, out = workspace
        _emit(workspace, _meta([
            {"url": f"https://example.invalid/{TARBALL[0]}",
             "sha256": _sha(TARBALL[1])},
            {"url": "https://vendor.invalid/tool.run", "filename": "tool.run",
             "sha256": "e" * 64, "redistributable": False},
        ]))
        _, payloads, _ = _read(workspace)
        assert "NON-REDISTRIBUTABLE.txt" in payloads["README.SOURCES"].decode()


class TestArchiveLayout:
    def test_top_directory_matches_the_documented_layout(self, workspace):
        # SOURCES.md §3: the archive contains <name>-<version>-<release>/.
        _emit(workspace, _meta())
        full_names, _, _ = _read(workspace)
        tops = {n.split("/")[0] for n in full_names}
        assert tops == {"openssl-3.6.0-2"}, \
            f"top directory drifted from the §3 layout: {tops}"


class TestDeterminismWithPromisedMembers:
    def test_two_runs_produce_identical_bytes(self):
        digests = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                pkg_dir = root / "packages" / "core" / "openssl"
                pkg_dir.mkdir(parents=True)
                (pkg_dir / "build.sh").write_text("#!/bin/bash\n:\n")
                (pkg_dir / "package.yml").write_text("name: openssl\n")
                sources = root / "sources"
                sources.mkdir()
                for fname, payload in (TARBALL, SECOND):
                    (sources / fname).write_bytes(payload)
                out = root / "out"
                out.mkdir()
                status, msg = _bsa.build_source_archive(
                    pkg_dir,
                    _meta([
                        {"url": f"https://example.invalid/{TARBALL[0]}",
                         "sha256": _sha(TARBALL[1])},
                        {"url": f"https://example.invalid/{SECOND[0]}",
                         "sha256": _sha(SECOND[1])},
                    ]),
                    sources, out, root)
                assert status == "ok", msg
                archive = out / "openssl-3.6.0-2.igos.src.tar.gz"
                digests.append(hashlib.sha256(archive.read_bytes()).hexdigest())
        assert digests[0] == digests[1], "two runs produced different bytes"
