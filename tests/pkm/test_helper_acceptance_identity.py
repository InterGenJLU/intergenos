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


def _writer(source):
    """Extract only the actual record-writing block, without any downloads."""
    match = re.search(
        r'(?:    ACCEPTANCE_USER=[^\n]*\n)?'
        r'(?:    ACCEPTANCE_IDENTITY=.*?\n)?'
        r'    cat > "\$ACCEPTANCE_FILE" <<JSON\n.*?\nJSON\n',
        source.read_text(), re.S,
    )
    assert match, f"No acceptance writer found in {source}"
    return match.group()


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
def test_record_writer_needs_no_new_library_api(source, tmp_path, monkeypatch):
    # A helper upgrade does not upgrade an already-installed library. The
    # record writer must therefore work without calling any new library API.
    result, record = _run_writer(
        source, tmp_path, monkeypatch, "alice", source_library=False,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(record.read_text())
    assert data["user"] == "alice"
    assert data["consenting_user_named"] is True
