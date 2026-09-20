"""A source is only called unfetchable after a bounded number of attempts.

Decided 2026-09-20. On 2026-09-19 a recipe's pinned tarball was recorded as
having no fetchable source after one failed wget and one failed curl inside the
same minute: the server answered 200 and then reset the stream partway through,
twice. Ten fetches the next morning all completed and hashed to exactly the
pinned sha256. The pin had been correct the whole time; the server had been
shedding load, and a cut was spent on a defect that did not exist.

One attempt cannot tell a server having a bad minute from a URL that is gone.
These tests pin the difference:

- the upstream leg is attempted more than once, with a pause between attempts;
- every attempt is printed, and the failure line says how many were made;
- a COMPLETE transfer whose bytes miss the pin is never retried — that is a
  definitive answer about what the server serves, and repeating it would only
  slow down a clear signal;
- the mirror leg keeps its single attempt, because it asks with -f and a 404
  there is an answer rather than a transient.
"""

import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "download-sources.py"
sys.path.insert(0, str(_PROJECT_ROOT))

spec = importlib.util.spec_from_file_location("download_sources_retry", SCRIPT_PATH)
_ds = importlib.util.module_from_spec(spec)
sys.modules["download_sources_retry"] = _ds
spec.loader.exec_module(_ds)

MIRROR_BASE = "https://repo.intergenos.org/sources/current"
PAYLOAD = b"\x1f\x8b" + b"pinned source bytes " * 128
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()
OTHER = b"\x1f\x8b" + b"different bytes " * 160
UPSTREAM_URL = "https://upstream.example.org/releases/thing-1.0.tar.gz"


class _Server:
    """Stands in for subprocess.run with a scripted sequence of answers.

    Each entry in `script` is what the NEXT upstream attempt does, in order:
      "fail"     the tool exits non-zero and writes nothing (refused/reset);
      "partial"  the tool exits non-zero having written a short prefix — the
                 2026-09-19 shape, a truncated transfer;
      "serve"    the tool exits 0 and writes the pinned bytes;
      "other"    the tool exits 0 and writes different, complete bytes.
    A mirror URL is answered with `mirror`, once; the mirror is never scripted
    per attempt because it is only ever asked once.
    """

    def __init__(self, script, mirror=None):
        self.script = list(script)
        self.mirror = mirror
        self.calls = []

    def __call__(self, argv, **kwargs):
        url = argv[-1]
        dest = argv[argv.index("-o") + 1] if "-o" in argv else argv[argv.index("-O") + 1]
        self.calls.append((argv[0], url))
        if url.startswith(MIRROR_BASE):
            if self.mirror is None:
                return subprocess.CompletedProcess(argv, 22, b"", b"404")
            Path(dest).write_bytes(self.mirror)
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        # Each upstream ATTEMPT tries wget and then curl, so one scripted step
        # covers both tools: it is consumed when the attempt's first tool runs.
        if argv[0] == "wget":
            self.step = self.script.pop(0) if self.script else "fail"
        step = getattr(self, "step", "fail")
        if step == "serve":
            Path(dest).write_bytes(PAYLOAD)
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        if step == "other":
            Path(dest).write_bytes(OTHER)
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        if step == "partial":
            Path(dest).write_bytes(PAYLOAD[: len(PAYLOAD) // 3])
            return subprocess.CompletedProcess(argv, 92, b"", b"stream reset by server")
        return subprocess.CompletedProcess(argv, 7, b"", b"connection refused")

    @property
    def upstream_attempts(self):
        return len([1 for tool, url in self.calls if tool == "wget"])


class TestABadMinuteIsNotADeadPin:
    def test_a_first_attempt_that_fails_is_retried_and_succeeds(self, tmp_path, monkeypatch):
        """THE DEFECT: one failure used to be the whole answer."""
        dest = tmp_path / "thing-1.0.tar.gz"
        srv = _Server(["fail", "serve"])
        monkeypatch.setattr(_ds.subprocess, "run", srv)

        assert _ds.download_file(UPSTREAM_URL, str(dest), expected_sha256=PAYLOAD_SHA)
        assert srv.upstream_attempts == 2
        assert dest.read_bytes() == PAYLOAD

    def test_a_truncated_transfer_is_retried(self, tmp_path, monkeypatch):
        """The 2026-09-19 shape: 200, then the stream is cut short."""
        dest = tmp_path / "thing-1.0.tar.gz"
        srv = _Server(["partial", "partial", "serve"])
        monkeypatch.setattr(_ds.subprocess, "run", srv)

        assert _ds.download_file(UPSTREAM_URL, str(dest), expected_sha256=PAYLOAD_SHA)
        assert srv.upstream_attempts == 3
        assert dest.read_bytes() == PAYLOAD

    def test_no_partial_survives_a_retry(self, tmp_path, monkeypatch):
        """A short prefix from a failed attempt is never left on disk."""
        dest = tmp_path / "thing-1.0.tar.gz"
        srv = _Server(["partial", "partial", "partial"])
        monkeypatch.setattr(_ds.subprocess, "run", srv)

        assert not _ds.download_file(UPSTREAM_URL, str(dest), expected_sha256=PAYLOAD_SHA)
        assert not dest.exists()

    def test_the_attempts_are_bounded_and_the_failure_says_how_many(
            self, tmp_path, monkeypatch, capsys):
        dest = tmp_path / "thing-1.0.tar.gz"
        srv = _Server(["fail", "fail", "fail", "serve"])
        monkeypatch.setattr(_ds.subprocess, "run", srv)

        assert not _ds.download_file(UPSTREAM_URL, str(dest), expected_sha256=PAYLOAD_SHA)
        assert srv.upstream_attempts == _ds.DEFAULT_UPSTREAM_ATTEMPTS == 3
        out = capsys.readouterr().out
        assert "attempt 1 of 3" in out
        assert "attempt 3 of 3" in out
        assert "FAILED after 3 upstream attempt(s)" in out

    def test_every_attempt_is_disclosed_by_url(self, tmp_path, monkeypatch, capsys):
        dest = tmp_path / "thing-1.0.tar.gz"
        srv = _Server(["fail", "serve"])
        monkeypatch.setattr(_ds.subprocess, "run", srv)

        _ds.download_file(UPSTREAM_URL, str(dest), expected_sha256=PAYLOAD_SHA)
        assert capsys.readouterr().out.count(UPSTREAM_URL) >= 2

    def test_the_attempt_count_is_configurable(self, tmp_path, monkeypatch):
        dest = tmp_path / "thing-1.0.tar.gz"
        monkeypatch.setenv("SOURCE_FETCH_ATTEMPTS", "5")
        srv = _Server(["fail", "fail", "fail", "fail", "serve"])
        monkeypatch.setattr(_ds.subprocess, "run", srv)

        assert _ds.download_file(UPSTREAM_URL, str(dest), expected_sha256=PAYLOAD_SHA)
        assert srv.upstream_attempts == 5

    def test_one_attempt_can_be_asked_for(self, tmp_path, monkeypatch):
        dest = tmp_path / "thing-1.0.tar.gz"
        monkeypatch.setenv("SOURCE_FETCH_ATTEMPTS", "1")
        srv = _Server(["fail", "serve"])
        monkeypatch.setattr(_ds.subprocess, "run", srv)

        assert not _ds.download_file(UPSTREAM_URL, str(dest), expected_sha256=PAYLOAD_SHA)
        assert srv.upstream_attempts == 1

    def test_a_nonsense_attempt_setting_falls_back_to_the_default(self, monkeypatch):
        monkeypatch.setenv("SOURCE_FETCH_ATTEMPTS", "not a number")
        assert _ds.source_fetch_attempts() == _ds.DEFAULT_UPSTREAM_ATTEMPTS
        monkeypatch.setenv("SOURCE_FETCH_ATTEMPTS", "0")
        assert _ds.source_fetch_attempts() == 1


class TestWhatIsNotRetried:
    def test_a_complete_transfer_that_misses_the_pin_is_not_retried(
            self, tmp_path, monkeypatch, capsys):
        """Different bytes are an answer, not weather."""
        dest = tmp_path / "thing-1.0.tar.gz"
        srv = _Server(["other", "serve"])
        monkeypatch.setattr(_ds.subprocess, "run", srv)

        assert not _ds.download_file(UPSTREAM_URL, str(dest), expected_sha256=PAYLOAD_SHA)
        assert srv.upstream_attempts == 1
        assert "NOT RETRIED" in capsys.readouterr().out
        assert not dest.exists()

    def test_the_mirror_is_asked_once_however_many_upstream_attempts_follow(
            self, tmp_path, monkeypatch):
        dest = tmp_path / "thing-1.0.tar.gz"
        srv = _Server(["fail", "fail", "fail"])
        monkeypatch.setattr(_ds.subprocess, "run", srv)

        assert not _ds.download_file(UPSTREAM_URL, str(dest), expected_sha256=PAYLOAD_SHA)
        mirror_calls = [u for _, u in srv.calls if u.startswith(MIRROR_BASE)]
        assert len(mirror_calls) == 1

    def test_a_mirror_hit_never_reaches_the_retry_loop(self, tmp_path, monkeypatch):
        dest = tmp_path / "thing-1.0.tar.gz"
        srv = _Server(["serve"], mirror=PAYLOAD)
        monkeypatch.setattr(_ds.subprocess, "run", srv)

        assert _ds.download_file(UPSTREAM_URL, str(dest), expected_sha256=PAYLOAD_SHA)
        assert srv.upstream_attempts == 0


class TestTheBackoff:
    def test_it_waits_between_attempts_and_the_wait_grows(self, tmp_path, monkeypatch):
        dest = tmp_path / "thing-1.0.tar.gz"
        monkeypatch.setenv("SOURCE_FETCH_BACKOFF", "1.5")
        slept = []
        monkeypatch.setattr(_ds.time, "sleep", slept.append)
        srv = _Server(["fail", "fail", "serve"])
        monkeypatch.setattr(_ds.subprocess, "run", srv)

        assert _ds.download_file(UPSTREAM_URL, str(dest), expected_sha256=PAYLOAD_SHA)
        assert slept == [1.5, 3.0]

    def test_it_does_not_wait_after_the_last_attempt(self, tmp_path, monkeypatch):
        dest = tmp_path / "thing-1.0.tar.gz"
        monkeypatch.setenv("SOURCE_FETCH_BACKOFF", "1.5")
        slept = []
        monkeypatch.setattr(_ds.time, "sleep", slept.append)
        srv = _Server(["fail", "fail", "fail"])
        monkeypatch.setattr(_ds.subprocess, "run", srv)

        assert not _ds.download_file(UPSTREAM_URL, str(dest), expected_sha256=PAYLOAD_SHA)
        assert len(slept) == 2

    def test_a_nonsense_backoff_setting_falls_back_to_the_default(self, monkeypatch):
        monkeypatch.setenv("SOURCE_FETCH_BACKOFF", "soon")
        assert _ds.source_fetch_backoff() == _ds.DEFAULT_UPSTREAM_BACKOFF_SECONDS
        monkeypatch.setenv("SOURCE_FETCH_BACKOFF", "-4")
        assert _ds.source_fetch_backoff() == 0.0


class TestTheStatedAttemptCountIsTheTruth:
    """wget retries 20 times by default, silently under -q.

    Measured 2026-09-20 against a local server that cut two transfers short:
    the project asked for ONE attempt and wget made THREE requests, so the
    failure line's attempt count would have described a fraction of what
    upstream was actually asked. The fetcher pins wget to a single try so the
    number it prints is the number of times upstream was contacted.
    """

    def test_the_wget_leg_asks_once_per_attempt(self, tmp_path, monkeypatch):
        dest = tmp_path / "thing-1.0.tar.gz"
        srv = _Server(["serve"])
        monkeypatch.setattr(_ds.subprocess, "run", srv)
        seen = []
        real = _ds.subprocess.run

        def spy(argv, **kwargs):
            seen.append(list(argv))
            return real(argv, **kwargs)

        monkeypatch.setattr(_ds.subprocess, "run", spy)
        _ds.download_file(UPSTREAM_URL, str(dest), expected_sha256=PAYLOAD_SHA)
        wget_argv = [a for a in seen if a and a[0] == "wget"]
        assert wget_argv, "the wget leg never ran"
        for argv in wget_argv:
            assert "--tries=1" in argv, argv
