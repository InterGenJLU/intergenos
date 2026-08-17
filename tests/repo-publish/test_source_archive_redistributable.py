"""build-source-archives.py must never republish a withheld vendor input.

A source entry marked `redistributable: false` is fetched and built against
but may not be published by us — the first is the NVIDIA CUDA toolkit runfile
that compute/llama-cpp-cuda compiles against, because nvcc is not
redistributable under NVIDIA's CUDA EULA.

Two properties are load-bearing and both are pinned here:

  1. the withheld file's bytes are NOT in the emitted source archive, and
  2. the archive SAYS SO — a NON-REDISTRIBUTABLE.txt naming the file, the
     vendor URL and the sha256 the build verified. A corresponding-source
     archive that is quietly incomplete is worse than one that is openly
     incomplete: the first misleads, the second tells you where to look.

Property 1 holds today partly because the generator bundles source[0] only.
That is an implementation detail, not a guarantee, and closing the
completeness gap it represents is exactly the change that would start
republishing a vendor installer if nothing stopped it. These tests are what
stops it, so they assert on the emitted archive's contents rather than on
which index the generator happens to read.
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

VENDOR_SHA256 = "07621d381f3252fe21b0c5c5a76a2e8061abb861f2c23ad8396b97d18a93ca1e"  # sha256 of the test payload — when the entry is
# republishable (the no-note case below) the bundling verifies pins at emission
UPSTREAM_SHA256 = "86d278e11f320200fa792c42fed8251d57a680a8b2c09aee85209627f7687080"  # sha256 of the test payload — pins are verified at emission
VENDOR_URL = ("https://developer.download.nvidia.com/compute/cuda/13.3.1/"
              "local_installers/cuda_13.3.1_610.43.02_linux.run")
VENDOR_FILE = "cuda_13.3.1_610.43.02_linux.run"
VENDOR_BYTES = b"PROPRIETARY-VENDOR-INSTALLER-PAYLOAD"


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        pkg_dir = root / "packages" / "compute" / "demo-engine"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "build.sh").write_text("#!/bin/bash\n:\n")
        (pkg_dir / "package.yml").write_text("name: demo-engine\n")

        sources = root / "sources"
        sources.mkdir()
        (sources / "llama.cpp-b8796.tar.gz").write_bytes(b"UPSTREAM-MIT-SOURCE")
        # Present in the sources dir exactly as it is on a real builder: the
        # generator must decline to bundle it because of the declaration, not
        # because the file happens to be absent.
        (sources / VENDOR_FILE).write_bytes(VENDOR_BYTES)

        out = root / "out"
        out.mkdir()
        yield root, pkg_dir, sources, out


def _meta(withheld=True):
    vendor = {
        "url": VENDOR_URL,
        "filename": VENDOR_FILE,
        "sha256": VENDOR_SHA256,
        "extract": False,
    }
    if withheld:
        vendor["redistributable"] = False
    return {
        "name": "demo-engine",
        "version": "b8796",
        "release": 1,
        "source": [
            {
                "url": "https://github.com/ggml-org/llama.cpp/archive/x.tar.gz",
                "filename": "llama.cpp-b8796.tar.gz",
                "sha256": UPSTREAM_SHA256,
            },
            vendor,
        ],
    }


def _emit(workspace, meta):
    root, pkg_dir, sources, out = workspace
    status, msg = _bsa.build_source_archive(pkg_dir, meta, sources, out, root)
    assert status == "ok", msg
    archive = out / "demo-engine-b8796-1.igos.src.tar.gz"
    assert archive.is_file()
    with tarfile.open(archive, "r:gz") as tf:
        names = [Path(n).name for n in tf.getnames()]
        payloads = {
            Path(m.name).name: tf.extractfile(m).read()
            for m in tf.getmembers() if m.isfile()
        }
    return names, payloads


class TestWithheldSourceIsNotRepublished:
    def test_vendor_bytes_absent_from_the_archive(self, workspace):
        names, payloads = _emit(workspace, _meta(withheld=True))
        assert VENDOR_FILE not in names
        assert VENDOR_BYTES not in b"".join(payloads.values())

    def test_redistributable_upstream_source_still_bundled(self, workspace):
        # The exclusion is per entry. Withholding the vendor input must not
        # cost the package its actual corresponding source.
        names, _ = _emit(workspace, _meta(withheld=True))
        assert "llama.cpp-b8796.tar.gz" in names
        assert "build.sh" in names
        assert "package.yml" in names

    def test_archive_states_what_is_missing_and_where_to_get_it(self, workspace):
        names, payloads = _emit(workspace, _meta(withheld=True))
        assert "NON-REDISTRIBUTABLE.txt" in names
        note = payloads["NON-REDISTRIBUTABLE.txt"].decode()
        assert VENDOR_FILE in note
        assert VENDOR_URL in note
        assert VENDOR_SHA256 in note

    def test_no_note_when_nothing_is_withheld(self, workspace):
        # The note must mean something. Emitting it unconditionally would make
        # every archive claim an omission it does not have.
        names, _ = _emit(workspace, _meta(withheld=False))
        assert "NON-REDISTRIBUTABLE.txt" not in names

    def test_withheld_source_zero_is_not_bundled_either(self, workspace):
        # Defensive: if a package's FIRST source is the withheld one, the
        # generator must still refuse it rather than fall through the
        # source[0]-is-the-upstream-tarball assumption.
        root, pkg_dir, sources, out = workspace
        meta = _meta(withheld=True)
        meta["source"] = [meta["source"][1]]
        names, payloads = _emit(workspace, meta)
        assert VENDOR_FILE not in names
        assert VENDOR_BYTES not in b"".join(payloads.values())
        assert "NON-REDISTRIBUTABLE.txt" in names
