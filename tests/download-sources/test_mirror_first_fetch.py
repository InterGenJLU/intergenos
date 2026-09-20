"""The source fetcher tries the project mirror before upstream.

Decided 2026-09-20: software comes mirror-first, with any upstream pull
disclosed in the same run. Before this, download-sources.py tried upstream
first and fell back to the mirror only when wget AND curl both failed, so an
ordinary run pulled from the internet even when the project mirror already
served a pinned, byte-identical copy.

The sha256 pin is enforced on BOTH paths, so the order changes where the bytes
come from, never whether they are verified.
"""

import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "download-sources.py"
sys.path.insert(0, str(_PROJECT_ROOT))

spec = importlib.util.spec_from_file_location("download_sources_order", SCRIPT_PATH)
_ds = importlib.util.module_from_spec(spec)
sys.modules["download_sources_order"] = _ds
spec.loader.exec_module(_ds)


# The base names this URL only inside download_file; the tests name it in
# full so that a base run fails on the ORDER of the fetches, not on a
# missing module attribute.
MIRROR_BASE = "https://repo.intergenos.org/sources/current"

PAYLOAD = b"\x1f\x8b" + b"pinned source bytes " * 128
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()
UPSTREAM_URL = "https://upstream.example.org/releases/thing-1.0.tar.gz"


class _Recorder:
    """Stands in for subprocess.run and records every attempt in order."""

    def __init__(self, serve: dict):
        # serve maps a URL to the bytes it answers with; a URL absent from the
        # map answers "unreachable" (non-zero exit, nothing written).
        self.serve = serve
        self.attempts = []

    def __call__(self, argv, **kwargs):
        url = argv[-1]
        dest = argv[argv.index("-o") + 1] if "-o" in argv else argv[argv.index("-O") + 1]
        self.attempts.append((argv[0], url))
        if url in self.serve:
            Path(dest).write_bytes(self.serve[url])
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        return subprocess.CompletedProcess(argv, 8, b"", b"not found")


def _mirror_url(filename: str) -> str:
    return f"{MIRROR_BASE}/{filename}"


class TestFetchOrder:
    def test_mirror_is_attempted_before_upstream(self, tmp_path, monkeypatch):
        """With both sides serving the file, the first attempt is the mirror."""
        dest = tmp_path / "thing-1.0.tar.gz"
        rec = _Recorder({UPSTREAM_URL: PAYLOAD, _mirror_url(dest.name): PAYLOAD})
        monkeypatch.setattr(_ds.subprocess, "run", rec)

        assert _ds.download_file(UPSTREAM_URL, str(dest), expected_sha256=PAYLOAD_SHA)
        assert rec.attempts, "no fetch was attempted at all"
        assert rec.attempts[0][1] == _mirror_url(dest.name), (
            f"first attempt was {rec.attempts[0][1]}, not the mirror")
        assert UPSTREAM_URL not in [u for _, u in rec.attempts], (
            "upstream was contacted although the mirror served the file")
        assert dest.read_bytes() == PAYLOAD

    def test_mirror_miss_falls_back_to_upstream_and_says_so(self, tmp_path, monkeypatch, capsys):
        """A name the mirror lacks falls through to upstream, disclosed."""
        dest = tmp_path / "thing-1.0.tar.gz"
        rec = _Recorder({UPSTREAM_URL: PAYLOAD})
        monkeypatch.setattr(_ds.subprocess, "run", rec)

        assert _ds.download_file(UPSTREAM_URL, str(dest), expected_sha256=PAYLOAD_SHA)
        urls = [u for _, u in rec.attempts]
        assert urls[0] == _mirror_url(dest.name)
        assert UPSTREAM_URL in urls
        out = capsys.readouterr().out
        assert UPSTREAM_URL in out, "the upstream pull was not disclosed"
        assert dest.read_bytes() == PAYLOAD

    def test_pin_is_enforced_on_the_mirror_path(self, tmp_path, monkeypatch):
        """Mirror bytes that miss the pin are rejected, not written."""
        dest = tmp_path / "thing-1.0.tar.gz"
        rec = _Recorder({_mirror_url(dest.name): b"wrong bytes " * 256})
        monkeypatch.setattr(_ds.subprocess, "run", rec)

        assert not _ds.download_file(UPSTREAM_URL, str(dest), expected_sha256=PAYLOAD_SHA)
        assert not dest.exists()

    def test_pin_is_enforced_on_the_upstream_path(self, tmp_path, monkeypatch):
        """Upstream bytes that miss the pin are rejected, not written."""
        dest = tmp_path / "thing-1.0.tar.gz"
        rec = _Recorder({UPSTREAM_URL: b"wrong bytes " * 256})
        monkeypatch.setattr(_ds.subprocess, "run", rec)

        assert not _ds.download_file(UPSTREAM_URL, str(dest), expected_sha256=PAYLOAD_SHA)
        assert not dest.exists()

    def test_unpinned_source_skips_the_mirror(self, tmp_path, monkeypatch):
        """With no pin to verify against, the mirror is no safer than upstream."""
        dest = tmp_path / "thing-1.0.tar.gz"
        rec = _Recorder({UPSTREAM_URL: PAYLOAD, _mirror_url(dest.name): PAYLOAD})
        monkeypatch.setattr(_ds.subprocess, "run", rec)

        assert _ds.download_file(UPSTREAM_URL, str(dest), expected_sha256="NEEDS_CHECKSUM")
        assert rec.attempts[0][1] == UPSTREAM_URL
        assert _mirror_url(dest.name) not in [u for _, u in rec.attempts]

    def test_a_source_hosted_on_the_mirror_is_not_fetched_twice(self, tmp_path, monkeypatch):
        """A recipe whose own url IS the mirror url makes one attempt, not two."""
        dest = tmp_path / "thing-1.0.tar.gz"
        same = _mirror_url(dest.name)
        rec = _Recorder({same: PAYLOAD})
        monkeypatch.setattr(_ds.subprocess, "run", rec)

        assert _ds.download_file(same, str(dest), expected_sha256=PAYLOAD_SHA)
        assert [u for _, u in rec.attempts] == [same]

    def test_fetch_base_override_is_honoured(self, tmp_path, monkeypatch):
        """MIRROR_FETCH_BASE still redirects the mirror leg."""
        dest = tmp_path / "thing-1.0.tar.gz"
        monkeypatch.setenv("MIRROR_FETCH_BASE", "https://mirror.example.net/src/current/")
        served = f"https://mirror.example.net/src/current/{dest.name}"
        rec = _Recorder({served: PAYLOAD})
        monkeypatch.setattr(_ds.subprocess, "run", rec)

        assert _ds.download_file(UPSTREAM_URL, str(dest), expected_sha256=PAYLOAD_SHA)
        assert rec.attempts[0][1] == served
