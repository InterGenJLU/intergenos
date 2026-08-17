"""build-source-archives.py must bundle EVERY declared source, not only the first.

A from-source distribution's source archive is its corresponding-source artifact:
it is the answer to "what was this built from". The generator used to copy
``source[0]`` and stop, so a package declaring several inputs published an
archive that named all of them in its package.yml and carried one. Measured on
the tree at the time of this change: 52 packages declared 162 secondary source
entries, of which 161 were republishable and absent from the archives and 1 was
the deliberately withheld vendor installer described in
test_source_archive_redistributable.py.

What is pinned here:

  1. every republishable declared entry lands in the archive;
  2. the withheld-entry exclusion still holds when the bundling is complete —
     that exclusion used to be an accident of reading index 0 only, and this is
     the change that would have turned it into a republication;
  3. the archive is never emitted SHORT: if a declared republishable input is
     not staged, the package is skipped and every absent filename is named,
     because an archive quietly missing an input is worse than no archive;
  4. two entries resolving to one stored filename are refused, not silently
     collapsed to whichever was copied last;
  5. the bytes stay reproducible — the mirror publish hardlinks unchanged
     archives, so a nondeterministic writer forces a full re-upload of the whole
     corresponding-source corpus.

The assertions read the emitted archive, not the generator's internals, so they
keep their meaning if the staging is rewritten again.
"""

import importlib.util
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "build-source-archives.py"

spec = importlib.util.spec_from_file_location("build_source_archives", SCRIPT_PATH)
_bsa = importlib.util.module_from_spec(spec)
sys.modules["build_source_archives"] = _bsa
spec.loader.exec_module(_bsa)

# A three-input package modelled on the real multi-source shape in the tree
# (a primary tarball plus companion tarballs the build compiles in).
PRIMARY = ("gcc-15.2.0.tar.xz", b"PRIMARY-COMPILER-SOURCE")
COMPANION_A = ("gmp-6.3.0.tar.xz", b"COMPANION-GMP-SOURCE")
COMPANION_B = ("mpfr-4.2.1.tar.xz", b"COMPANION-MPFR-SOURCE")
ALL_INPUTS = [PRIMARY, COMPANION_A, COMPANION_B]


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        pkg_dir = root / "packages" / "toolchain" / "demo-multi"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "build.sh").write_text("#!/bin/bash\n:\n")
        (pkg_dir / "package.yml").write_text("name: demo-multi\n")
        patches = pkg_dir / "patches"
        patches.mkdir()
        (patches / "0001-demo.patch").write_text("--- a\n+++ b\n")

        sources = root / "sources"
        sources.mkdir()
        for fname, payload in ALL_INPUTS:
            (sources / fname).write_bytes(payload)

        out = root / "out"
        out.mkdir()
        yield root, pkg_dir, sources, out


def _meta(entries=None):
    return {
        "name": "demo-multi",
        "version": "15.2.0",
        "release": 1,
        "source": entries if entries is not None else [
            {"url": f"https://example.invalid/{PRIMARY[0]}", "sha256": "0d1ac36626e1be711f61ca2994b41d95d79a459084e74f6d5330e51e101da74b"},
            {"url": f"https://example.invalid/{COMPANION_A[0]}", "sha256": "ce9a162fd9706f802e07ba651c6ea2467b719c5394943d78f2ff62433b6dfc84"},
            {"url": f"https://example.invalid/{COMPANION_B[0]}", "sha256": "eed92bd085dd3e01cb9b9e5a6773c7cf573549dd42a860849bd198a1b75b9db2"},
        ],
    }


def _emit(workspace, meta, expect="ok"):
    root, pkg_dir, sources, out = workspace
    status, msg = _bsa.build_source_archive(pkg_dir, meta, sources, out, root)
    assert status == expect, f"expected {expect}, got {status}: {msg}"
    return msg


def _read(workspace, archive_name="demo-multi-15.2.0-1.igos.src.tar.gz"):
    _, _, _, out = workspace
    archive = out / archive_name
    assert archive.is_file(), f"no archive at {archive}"
    with tarfile.open(archive, "r:gz") as tf:
        names = [Path(n).name for n in tf.getnames()]
        payloads = {
            Path(m.name).name: tf.extractfile(m).read()
            for m in tf.getmembers() if m.isfile()
        }
    return names, payloads, archive


class TestEveryDeclaredSourceIsBundled:
    def test_all_three_declared_tarballs_are_present(self, workspace):
        _emit(workspace, _meta())
        names, payloads, _ = _read(workspace)
        for fname, expected in ALL_INPUTS:
            assert fname in names, f"{fname} missing from the source archive"
            assert payloads[fname] == expected, f"{fname} carries the wrong bytes"

    def test_the_rest_of_the_archive_is_unchanged(self, workspace):
        # Completeness must not cost the archive the parts it already carried.
        _emit(workspace, _meta())
        names, _, _ = _read(workspace)
        assert "build.sh" in names
        assert "package.yml" in names
        assert "0001-demo.patch" in names

    def test_the_status_line_states_the_count(self, workspace):
        # A publish log should show completeness per package, not just success.
        msg = _emit(workspace, _meta())
        assert "3 of 3 declared source(s)" in msg

    def test_a_single_source_package_still_works(self, workspace):
        msg = _emit(workspace, _meta([
            {"url": f"https://example.invalid/{PRIMARY[0]}", "sha256": "0d1ac36626e1be711f61ca2994b41d95d79a459084e74f6d5330e51e101da74b"},
        ]))
        names, _, _ = _read(workspace)
        assert PRIMARY[0] in names
        assert COMPANION_A[0] not in names
        assert "1 of 1 declared source(s)" in msg

    def test_filename_override_is_honoured_on_secondary_entries(self, workspace):
        # The stored name, not the URL basename, is what the builder cached and
        # what verify-sources hashed — secondary entries must get the same rule.
        root, pkg_dir, sources, out = workspace
        (sources / "renamed-companion.tar.gz").write_bytes(b"RENAMED-COMPANION")
        _emit(workspace, _meta([
            {"url": f"https://example.invalid/{PRIMARY[0]}", "sha256": "0d1ac36626e1be711f61ca2994b41d95d79a459084e74f6d5330e51e101da74b"},
            {"url": "https://example.invalid/archive/v1.tar.gz",
             "filename": "renamed-companion.tar.gz", "sha256": "d0669e049d5872bc2432f55d52221f440953346b9d05578638320846620e6ad0"},
        ]))
        names, payloads, _ = _read(workspace)
        assert "renamed-companion.tar.gz" in names
        assert payloads["renamed-companion.tar.gz"] == b"RENAMED-COMPANION"
        assert "v1.tar.gz" not in names


class TestWithheldEntriesStayWithheldUnderCompleteBundling:
    """The exclusion must survive the completeness fix — see the module docstring
    of test_source_archive_redistributable.py for why this is load-bearing."""

    VENDOR_FILE = "vendor-installer.run"
    VENDOR_BYTES = b"PROPRIETARY-VENDOR-INSTALLER-PAYLOAD"
    VENDOR_URL = "https://vendor.invalid/downloads/vendor-installer.run"
    VENDOR_SHA = "e" * 64

    def _meta_with_vendor(self):
        return _meta([
            {"url": f"https://example.invalid/{PRIMARY[0]}", "sha256": "0d1ac36626e1be711f61ca2994b41d95d79a459084e74f6d5330e51e101da74b"},
            {"url": f"https://example.invalid/{COMPANION_A[0]}", "sha256": "ce9a162fd9706f802e07ba651c6ea2467b719c5394943d78f2ff62433b6dfc84"},
            {"url": self.VENDOR_URL, "filename": self.VENDOR_FILE,
             "sha256": self.VENDOR_SHA, "extract": False,
             "redistributable": False},
        ])

    @pytest.fixture
    def workspace_with_vendor(self, workspace):
        root, pkg_dir, sources, out = workspace
        # Present on disk exactly as on a real builder: the refusal must come
        # from the declaration, not from the file being absent.
        (sources / self.VENDOR_FILE).write_bytes(self.VENDOR_BYTES)
        return workspace

    def test_vendor_bytes_are_not_republished(self, workspace_with_vendor):
        _emit(workspace_with_vendor, self._meta_with_vendor())
        names, payloads, _ = _read(workspace_with_vendor)
        assert self.VENDOR_FILE not in names
        assert self.VENDOR_BYTES not in b"".join(payloads.values())

    def test_the_republishable_companions_are_still_complete(self, workspace_with_vendor):
        msg = _emit(workspace_with_vendor, self._meta_with_vendor())
        names, _, _ = _read(workspace_with_vendor)
        assert PRIMARY[0] in names
        assert COMPANION_A[0] in names
        assert "2 of 3 declared source(s), 1 withheld and stated" in msg

    def test_the_omission_is_stated(self, workspace_with_vendor):
        _emit(workspace_with_vendor, self._meta_with_vendor())
        names, payloads, _ = _read(workspace_with_vendor)
        assert "NON-REDISTRIBUTABLE.txt" in names
        note = payloads["NON-REDISTRIBUTABLE.txt"].decode()
        assert self.VENDOR_FILE in note
        assert self.VENDOR_URL in note
        assert self.VENDOR_SHA in note

    def test_a_withheld_entry_does_not_make_the_package_skip(self, workspace):
        # The withheld file is NOT staged here. Its absence must not be read as
        # "an input is missing" — we were never going to copy it.
        msg = _emit(workspace, self._meta_with_vendor())
        names, _, _ = _read(workspace)
        assert "NON-REDISTRIBUTABLE.txt" in names
        assert "2 of 3 declared source(s), 1 withheld and stated" in msg


class TestAnIncompleteArchiveIsNeverEmitted:
    def test_a_missing_secondary_input_skips_the_package(self, workspace):
        root, pkg_dir, sources, out = workspace
        (sources / COMPANION_B[0]).unlink()
        msg = _emit(workspace, _meta(), expect="skip")
        assert COMPANION_B[0] in msg
        assert not list(out.iterdir()), "no archive may be written on a skip"

    def test_the_skip_message_names_every_absent_input(self, workspace):
        root, pkg_dir, sources, out = workspace
        (sources / COMPANION_A[0]).unlink()
        (sources / COMPANION_B[0]).unlink()
        msg = _emit(workspace, _meta(), expect="skip")
        assert COMPANION_A[0] in msg
        assert COMPANION_B[0] in msg

    def test_a_missing_primary_input_still_skips(self, workspace):
        root, pkg_dir, sources, out = workspace
        (sources / PRIMARY[0]).unlink()
        msg = _emit(workspace, _meta(), expect="skip")
        assert PRIMARY[0] in msg


class TestMalformedDeclarationsAreRefused:
    def test_a_secondary_entry_without_a_url_is_named_by_index(self, workspace):
        msg = _emit(workspace, _meta([
            {"url": f"https://example.invalid/{PRIMARY[0]}", "sha256": "0d1ac36626e1be711f61ca2994b41d95d79a459084e74f6d5330e51e101da74b"},
            {"sha256": "ce9a162fd9706f802e07ba651c6ea2467b719c5394943d78f2ff62433b6dfc84"},
        ]), expect="fail")
        assert "source[1]" in msg
        assert "url" in msg

    def test_a_non_mapping_entry_is_named_by_index(self, workspace):
        msg = _emit(workspace, _meta([
            {"url": f"https://example.invalid/{PRIMARY[0]}", "sha256": "0d1ac36626e1be711f61ca2994b41d95d79a459084e74f6d5330e51e101da74b"},
            f"https://example.invalid/{COMPANION_A[0]}",
        ]), expect="fail")
        assert "source[1]" in msg

    def test_two_entries_with_one_stored_name_are_refused(self, workspace):
        # Silently copying one over the other would produce an archive that
        # claims two inputs and carries one.
        msg = _emit(workspace, _meta([
            {"url": f"https://example.invalid/{PRIMARY[0]}", "sha256": "0d1ac36626e1be711f61ca2994b41d95d79a459084e74f6d5330e51e101da74b"},
            {"url": f"https://mirror.invalid/other/{PRIMARY[0]}", "sha256": "ce9a162fd9706f802e07ba651c6ea2467b719c5394943d78f2ff62433b6dfc84"},
        ]), expect="fail")
        assert PRIMARY[0] in msg
        assert "filename" in msg


class TestDeterminismSurvivesCompleteBundling:
    """Two independent runs must produce identical bytes. Checked on the three
    shapes the change touches: multi-source, single-source, and withheld."""

    def _emit_twice(self, meta):
        digests = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                pkg_dir = root / "packages" / "toolchain" / "demo-multi"
                pkg_dir.mkdir(parents=True)
                (pkg_dir / "build.sh").write_text("#!/bin/bash\n:\n")
                (pkg_dir / "package.yml").write_text("name: demo-multi\n")
                sources = root / "sources"
                sources.mkdir()
                for fname, payload in ALL_INPUTS:
                    (sources / fname).write_bytes(payload)
                (sources / "vendor-installer.run").write_bytes(b"VENDOR")
                out = root / "out"
                out.mkdir()
                status, msg = _bsa.build_source_archive(
                    pkg_dir, meta, sources, out, root)
                assert status == "ok", msg
                archive = out / "demo-multi-15.2.0-1.igos.src.tar.gz"
                import hashlib
                digests.append(hashlib.sha256(archive.read_bytes()).hexdigest())
        return digests

    def test_multi_source_archive_is_byte_reproducible(self):
        a, b = self._emit_twice(_meta())
        assert a == b, "two runs produced different bytes"

    def test_single_source_archive_is_byte_reproducible(self):
        a, b = self._emit_twice(_meta([
            {"url": f"https://example.invalid/{PRIMARY[0]}", "sha256": "0d1ac36626e1be711f61ca2994b41d95d79a459084e74f6d5330e51e101da74b"},
        ]))
        assert a == b

    def test_withheld_source_archive_is_byte_reproducible(self):
        a, b = self._emit_twice(_meta([
            {"url": f"https://example.invalid/{PRIMARY[0]}", "sha256": "0d1ac36626e1be711f61ca2994b41d95d79a459084e74f6d5330e51e101da74b"},
            {"url": f"https://example.invalid/{COMPANION_A[0]}", "sha256": "ce9a162fd9706f802e07ba651c6ea2467b719c5394943d78f2ff62433b6dfc84"},
            {"url": "https://vendor.invalid/vendor-installer.run",
             "filename": "vendor-installer.run", "sha256": "e" * 64,
             "extract": False, "redistributable": False},
        ]))
        assert a == b

    def test_declaration_order_does_not_change_the_bytes(self):
        # The archive is a set of inputs, not a sequence. Reordering the
        # declaration must not force the mirror to re-upload the archive.
        forward = self._emit_twice(_meta())[0]
        reordered = self._emit_twice(_meta([
            {"url": f"https://example.invalid/{COMPANION_B[0]}", "sha256": "eed92bd085dd3e01cb9b9e5a6773c7cf573549dd42a860849bd198a1b75b9db2"},
            {"url": f"https://example.invalid/{PRIMARY[0]}", "sha256": "0d1ac36626e1be711f61ca2994b41d95d79a459084e74f6d5330e51e101da74b"},
            {"url": f"https://example.invalid/{COMPANION_A[0]}", "sha256": "ce9a162fd9706f802e07ba651c6ea2467b719c5394943d78f2ff62433b6dfc84"},
        ]))[0]
        assert forward == reordered
