"""An upload is not finished until the served directory's own index describes it.

Measured on 2026-09-20: the served source directory held 802 names and its
SHA256SUMS had been written on 2026-05-30. Hundreds of files had been published
since, and none of them were in it. Nothing noticed, because nothing looked —
the upload generated a sums file for the files IT brought and sent that along,
so the index described the last upload rather than the directory.

A checksum file that falls behind the directory it describes is worse than
none: it reads as verification while verifying a subset nobody has counted.

So the upload now ends by re-deriving SHA256SUMS on the host over the WHOLE
served directory and reading it back over the https URL a user reads. Both
steps fail the command rather than warning, because a run that reports success
while the index still describes the old directory is the exact silent drift
being closed.
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

spec = importlib.util.spec_from_file_location("download_sources_index", SCRIPT_PATH)
_ds = importlib.util.module_from_spec(spec)
sys.modules["download_sources_index"] = _ds
spec.loader.exec_module(_ds)

SUMS_BODY = b"".join(f"{'a' * 64}  file-{i}.tar.gz\n".encode() for i in range(1167))
SUMS_SHA = hashlib.sha256(SUMS_BODY).hexdigest()


class _Host:
    """Records every command the upload runs and answers like a healthy host."""

    def __init__(self, ssh_rc=0, ssh_out="1167\n", curl_rc=0, curl_body=SUMS_BODY):
        self.ssh_rc, self.ssh_out = ssh_rc, ssh_out
        self.curl_rc, self.curl_body = curl_rc, curl_body
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        tool = argv[0]
        if tool == "rsync":
            return subprocess.CompletedProcess(argv, 0, "sent 0 bytes", "")
        if tool == "ssh":
            return subprocess.CompletedProcess(argv, self.ssh_rc, self.ssh_out, "boom")
        if tool == "curl":
            return subprocess.CompletedProcess(argv, self.curl_rc, self.curl_body, b"")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def of(self, tool):
        return [a for a in self.calls if a and a[0] == tool]


class TestTheUploadRegeneratesTheServedIndex:
    def test_the_upload_runs_the_regeneration_on_the_host(self, monkeypatch, capsys):
        """THE DEFECT: the upload used to stop at the rsync."""
        host = _Host()
        monkeypatch.setattr(_ds.subprocess, "run", host)
        _ds.cmd_mirror_upload(["desktop"], mirror_host="intergenos@example.org")

        ssh_calls = host.of("ssh")
        assert ssh_calls, "the upload never asked the host to regenerate anything"
        script = ssh_calls[-1][-1]
        assert "sha256sum" in script
        assert "SHA256SUMS" in script
        assert _ds.mirror_upload_dir(_ds.DEFAULT_MIRROR_PATH) in script
        out = capsys.readouterr().out
        assert "SHA256SUMS regenerated: 1167 lines" in out

    def test_the_regeneration_covers_the_whole_directory_not_the_upload(self, monkeypatch):
        host = _Host()
        monkeypatch.setattr(_ds.subprocess, "run", host)
        _ds.cmd_mirror_upload(["desktop"], mirror_host="intergenos@example.org")

        script = host.of("ssh")[-1][-1]
        # It lists the served directory itself; it does not take a file list
        # from the upload, which is what made the old index a subset.
        assert "ls -A" in script
        assert "grep -v '^SHA256SUMS$'" in script

    def test_the_index_is_read_back_over_the_url_a_user_reads(self, monkeypatch, capsys):
        host = _Host()
        monkeypatch.setattr(_ds.subprocess, "run", host)
        _ds.cmd_mirror_upload(["desktop"], mirror_host="intergenos@example.org")

        curl_calls = host.of("curl")
        assert curl_calls, "the index was never read back"
        assert curl_calls[-1][-1] == f"{_ds.DEFAULT_MIRROR_FETCH_BASE}/SHA256SUMS"
        out = capsys.readouterr().out
        assert "1167 lines" in out
        assert SUMS_SHA in out

    def test_the_read_back_is_https_only(self, monkeypatch):
        host = _Host()
        monkeypatch.setattr(_ds.subprocess, "run", host)
        _ds.cmd_mirror_upload(["desktop"], mirror_host="intergenos@example.org")
        argv = host.of("curl")[-1]
        assert "--proto" in argv and argv[argv.index("--proto") + 1] == "=https"


class TestItFailsRatherThanWarns:
    def test_a_failed_regeneration_fails_the_upload(self, monkeypatch, capsys):
        host = _Host(ssh_rc=255, ssh_out="")
        monkeypatch.setattr(_ds.subprocess, "run", host)
        with pytest.raises(SystemExit) as e:
            _ds.cmd_mirror_upload(["desktop"], mirror_host="intergenos@example.org")
        assert e.value.code == 1
        assert "UPLOAD INCOMPLETE" in capsys.readouterr().out

    def test_a_regeneration_that_prints_no_count_fails_the_upload(self, monkeypatch):
        host = _Host(ssh_out="something went sideways\n")
        monkeypatch.setattr(_ds.subprocess, "run", host)
        with pytest.raises(SystemExit) as e:
            _ds.cmd_mirror_upload(["desktop"], mirror_host="intergenos@example.org")
        assert e.value.code == 1

    def test_an_unreadable_index_fails_the_upload(self, monkeypatch, capsys):
        host = _Host(curl_rc=22, curl_body=b"")
        monkeypatch.setattr(_ds.subprocess, "run", host)
        with pytest.raises(SystemExit) as e:
            _ds.cmd_mirror_upload(["desktop"], mirror_host="intergenos@example.org")
        assert e.value.code == 1
        assert "could not be read back" in capsys.readouterr().out

    def test_a_served_copy_with_a_different_line_count_fails_the_upload(self, monkeypatch, capsys):
        """The host wrote one directory and the web server serves another."""
        host = _Host(curl_body=b"".join(f"{'b' * 64}  f{i}\n".encode() for i in range(9)))
        monkeypatch.setattr(_ds.subprocess, "run", host)
        with pytest.raises(SystemExit) as e:
            _ds.cmd_mirror_upload(["desktop"], mirror_host="intergenos@example.org")
        assert e.value.code == 1
        assert "MISMATCH" in capsys.readouterr().out


class TestTheHelpersOnTheirOwn:
    def test_the_remote_path_is_quoted(self, monkeypatch):
        seen = []

        def spy(argv, **kwargs):
            seen.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, "12\n", "")

        monkeypatch.setattr(_ds.subprocess, "run", spy)
        _ds.regenerate_remote_sha256sums("u@h", "/a dir/with spaces", "2200", "/k")
        assert "'/a dir/with spaces'" in seen[-1][-1]

    def test_the_line_count_is_taken_from_the_output(self, monkeypatch):
        monkeypatch.setattr(
            _ds.subprocess, "run",
            lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "noise\n 42 \n", ""))
        assert _ds.regenerate_remote_sha256sums("u@h", "/d", "2200", "/k") == 42

    def test_a_failed_read_back_reports_minus_one(self, monkeypatch):
        monkeypatch.setattr(
            _ds.subprocess, "run",
            lambda argv, **kw: subprocess.CompletedProcess(argv, 22, b"", b""))
        assert _ds.read_back_sha256sums("https://example.org/x") == (-1, "")


class TestTheDryRunSaysItWould:
    def test_dry_run_names_the_regeneration_and_the_read_back(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--mirror-upload", "--dry-run",
             "--tier", "desktop"],
            capture_output=True, text=True, timeout=600,
        )
        assert result.returncode == 0, result.stderr
        assert "Would then re-derive SHA256SUMS" in result.stdout
        assert f"{_ds.DEFAULT_MIRROR_FETCH_BASE}/SHA256SUMS" in result.stdout

    def test_a_dry_run_touches_nothing(self, monkeypatch):
        host = _Host()
        monkeypatch.setattr(_ds.subprocess, "run", host)
        _ds.cmd_mirror_upload(["desktop"], mirror_host="intergenos@example.org", dry_run=True)
        assert host.of("ssh") == []
        assert host.of("curl") == []
        assert host.of("rsync") == []
