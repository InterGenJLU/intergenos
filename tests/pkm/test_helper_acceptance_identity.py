#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Run every download helper's real acceptance writer without its installer.

The shell identity query is controlled so these tests need neither root nor
sudo. The JSON writer, shared library and package-manager environment filter
run unchanged; all records are written into a temporary directory.
"""

import json
import re
import shlex
import subprocess
from pathlib import Path

import pytest

from pkm.installer import helper_environment


REPO = Path(__file__).resolve().parents[2]
LIBRARY = REPO / "packages/core/intergenos-helper-lib/helper-lib.sh"
EXTRA = (
    "brave", "chatgpt", "chrome", "claude-code", "codex", "discord", "edge",
    "ffmpeg-nonfree", "ge-proton", "signal", "spotify", "steam", "vscode", "zoom",
)
WRITERS = [REPO / f"packages/extra/{name}/build.sh" for name in EXTRA] + [
    REPO / "packages/compute/cuda-toolkit/helper/igos-install-cuda-toolkit",
]
EXPECTED_RECORDS = {
    "brave": {"version": "1.0", "payload_license": "LicenseRef-Brave-EULA"},
    "chatgpt": {"version": "1.0", "payload_license": "LicenseRef-OpenAI-Terms-of-Use"},
    "chrome": {"version": "1.0", "payload_license": "LicenseRef-Google-Chrome-ToS"},
    "claude-code": {
        "version": "1.0", "payload_license": "LicenseRef-Anthropic-Commercial-Terms",
    },
    "codex": {
        "version": "1.0", "payload_license": "Apache-2.0 AND LicenseRef-OpenAI-Terms-of-Use",
    },
    "discord": {
        "version": "1.0", "payload_license": "LicenseRef-Discord-ToS",
        "trust_anchor": "HTTPS-only (no cryptographic signature on tarball)",
        "trust_chain_caveat": (
            "Discord does not publish a signed apt repository; the Snap-Store "
            "alternative is rejected by the project-canonical no-snapd directive "
            "(decided 2026-05-21); the K21.F Option B trust-gap disclosure was "
            "presented and accepted at install time."
        ),
        "k21_f_option": "B",
    },
    "edge": {"version": "1.0", "payload_license": "LicenseRef-Microsoft-Edge-EULA"},
    "ffmpeg-nonfree": {
        "version": "1.0", "payload_license": "GPL-3.0-or-later AND FDK-AAC",
        "ffmpeg_version": "1", "ffmpeg_url_sha256": "fixture",
    },
    "ge-proton": {"version": "fixture", "payload_license": "LicenseRef-GE-Proton-Mixed"},
    "signal": {"version": "1.0", "payload_license": "AGPL-3.0-only"},
    "spotify": {"version": "1.0", "payload_license": "LicenseRef-Spotify-ToS"},
    "steam": {"version": "1.0", "payload_license": "LicenseRef-Valve-SSA"},
    "vscode": {
        "version": "1.0", "payload_license": "LicenseRef-Microsoft-VSCode-Software-License",
    },
    "zoom": {"version": "1.0", "payload_license": "LicenseRef-Zoom-Terms-of-Service"},
    "cuda-toolkit": {
        "version": "1", "payload_license": "LicenseRef-NVIDIA-CUDA-EULA",
        "license_text": "fixture.txt", "artifact": "fixture.run",
        "artifact_sha256": "fixture", "source_url": "https://example.invalid/fixture",
    },
}


def _writer(source):
    """Extract only the actual record-writing block, without any downloads."""
    text = source.read_text()
    assert "ACCEPTANCE_IDENTITY=" not in text
    assert 'cat > "$ACCEPTANCE_FILE"' not in text
    lines = text.splitlines(keepends=True)
    starts = [index for index, line in enumerate(lines)
              if re.match(r'\s*igos_helper_write_acceptance\s+"\$ACCEPTANCE_FILE"(?:\s|$)', line)]
    assert len(starts) == 1, f"Expected one acceptance writer in {source}"
    block = []
    for line in lines[starts[0]:]:
        block.append(line)
        if not line.rstrip("\r\n").endswith("\\"):
            break
    snippet = "".join(block)
    assert not snippet.rstrip().endswith("\\"), f"Unterminated acceptance writer in {source}"
    return snippet


def _assert_complete_record(source, data, sudo_user):
    helper = source.relative_to(REPO).parts[2]
    expected = {
        "helper": helper, **EXPECTED_RECORDS[helper],
        "user": sudo_user or "root",
        "consenting_user_named": bool(sudo_user),
        "user_source": "SUDO_USER" if sudo_user else "effective_uid",
    }
    assert set(data) == set(expected) | {"accepted_at"}
    assert {key: data[key] for key in expected} == expected
    assert data["consenting_user_named"] is bool(sudo_user)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", data["accepted_at"])


def _run_writer(source, tmp_path, monkeypatch, sudo_user, identity_fails=False,
                source_library=True, encoder_fails=False):
    monkeypatch.delenv("SUDO_USER", raising=False)
    if sudo_user is not None:
        monkeypatch.setenv("SUDO_USER", sudo_user)
    # These environment variables commonly describe the elevated process.
    monkeypatch.setenv("USER", "root")
    monkeypatch.setenv("LOGNAME", "root")
    env = helper_environment()
    record = tmp_path / "accepted.json"
    script = (
        "set -eu\n"
        + (f"source {shlex.quote(str(LIBRARY))}\n" if source_library else "")
        # Simulate the effective root identity only; the writer runs for real.
        + 'logname() { printf "%s\\n" root; }\n'
        + ('id() { return 1; }\n' if identity_fails else
           'id() { case "$1" in -u) printf "0\\n" ;; '
           '-un) printf "root\\n" ;; *) return 1 ;; esac; }\n')
        + ('python3() { return 1; }\n' if encoder_fails else '')
        + f"ACCEPTANCE_FILE={shlex.quote(str(record))}\n"
        + "CUDA_VERSION=1 CUDA_RUN=fixture.run CUDA_RUN_SHA256=fixture\n"
        + "CUDA_URL=https://example.invalid/fixture EULA_RECORD=fixture.txt\n"
        + "GE_TAG=fixture FFMPEG_VERSION=1 FFMPEG_SHA256=fixture\n"
        + _writer(source)
    )
    result = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-c", script],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=10,
    )
    print(f"writer={source.relative_to(REPO)} rc={result.returncode}")
    print(result.stdout, end="")
    print(result.stderr, end="")
    if record.exists():
        print(record.read_text())
    return result, record


@pytest.mark.parametrize("source", WRITERS, ids=lambda p: p.parent.name)
@pytest.mark.parametrize("sudo_user", ["alice", 'name"with\\escapes\nand-newline'])
def test_sudo_records_the_named_user(source, sudo_user, tmp_path, monkeypatch):
    result, record = _run_writer(source, tmp_path, monkeypatch, sudo_user)
    assert result.returncode == 0, result.stderr
    data = json.loads(record.read_text())
    _assert_complete_record(source, data, sudo_user)
    assert data["user"] == sudo_user
    assert data["consenting_user_named"] is True
    assert data["user_source"] == "SUDO_USER"


@pytest.mark.parametrize("source", WRITERS, ids=lambda p: p.parent.name)
@pytest.mark.parametrize("sudo_user", [None, ""])
def test_direct_root_explicitly_has_no_named_consenting_user(
    source, sudo_user, tmp_path, monkeypatch,
):
    result, record = _run_writer(source, tmp_path, monkeypatch, sudo_user)
    assert result.returncode == 0, result.stderr
    data = json.loads(record.read_text())
    _assert_complete_record(source, data, sudo_user)
    assert data["user"] == "root"
    assert data["consenting_user_named"] is False
    assert data["user_source"] == "effective_uid"


@pytest.mark.parametrize("source", WRITERS, ids=lambda p: p.parent.name)
@pytest.mark.parametrize("failure", ["lookup", "encoder"])
def test_unavailable_identity_does_not_write_acceptance(
    source, failure, tmp_path, monkeypatch,
):
    result, record = _run_writer(
        source, tmp_path, monkeypatch, None,
        identity_fails=failure == "lookup", encoder_fails=failure == "encoder",
    )
    assert result.returncode != 0
    assert not record.exists()


@pytest.mark.parametrize("source", WRITERS, ids=lambda p: p.parent.name)
def test_helper_refuses_an_older_library_with_an_update_instruction(source, tmp_path):
    guard = re.search(r'if ! declare -F igos_helper_write_acceptance.*?\nfi\n',
                      source.read_text(), re.S)
    assert guard, f"No library capability check in {source}"
    result = subprocess.run(["/bin/bash", "-c", guard.group()], cwd=tmp_path,
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "sudo pkm upgrade intergenos-helper-lib" in result.stderr


@pytest.mark.parametrize("source", WRITERS, ids=lambda p: p.parent.name)
def test_helper_specific_metadata_is_preserved(source, tmp_path, monkeypatch):
    result, record = _run_writer(source, tmp_path, monkeypatch, "alice")
    assert result.returncode == 0, result.stderr
    data = json.loads(record.read_text())
    _assert_complete_record(source, data, "alice")


def test_writer_extraction_keeps_a_third_continuation_line(tmp_path):
    source = tmp_path / "helper.sh"
    source.write_text(
        '    igos_helper_write_acceptance "$ACCEPTANCE_FILE" \\\n'
        '        sample 1.0 MIT \\\n'
        '        first_key first_value \\\n'
        '        second_key second_value\n'
        '    exit 37\n'
    )
    record = tmp_path / "accepted.json"
    snippet = _writer(source)
    result = subprocess.run(
        ["/bin/bash", "-c", f"set -eu\nsource {shlex.quote(str(LIBRARY))}\n"
         f"ACCEPTANCE_FILE={shlex.quote(str(record))}\n" + snippet],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(record.read_text())
    assert data["first_key"] == "first_value"
    assert data["second_key"] == "second_value"
    assert not snippet.rstrip().endswith("\\")


def test_writer_extraction_refuses_an_unterminated_continuation(tmp_path):
    source = tmp_path / "helper.sh"
    source.write_text(
        '    igos_helper_write_acceptance "$ACCEPTANCE_FILE" \\\n'
        '        sample 1.0 MIT \\\n'
    )
    with pytest.raises(AssertionError, match="[Uu]nterminated"):
        _writer(source)
