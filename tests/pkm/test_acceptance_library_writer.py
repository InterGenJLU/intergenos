# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The shared acceptance writer owns identity and publishes complete JSON."""

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

LIBRARY = Path(__file__).resolve().parents[2] / "packages/core/intergenos-helper-lib/helper-lib.sh"


def write_record(tmp_path, sudo_user=None, extra=(), preamble=""):
    env = dict(os.environ)
    env.pop("SUDO_USER", None)
    if sudo_user is not None:
        env["SUDO_USER"] = sudo_user
    path = tmp_path / "accepted.json"
    args = [str(path), "sample", '1.0"quoted', "MIT", *extra]
    command = (f"set -eu\nsource {shlex.quote(str(LIBRARY))}\n" + preamble
               + "igos_helper_write_acceptance " + shlex.join(args))
    result = subprocess.run(["/bin/bash", "-c", command], env=env,
                            capture_output=True, text=True)
    return result, path


@pytest.mark.parametrize("sudo_user", ["alice", 'name"with\\escapes\nand-newline'])
def test_named_user_and_metadata_are_encoded_before_publication(tmp_path, sudo_user):
    result, path = write_record(tmp_path, sudo_user, ("license_text", 'a"b\\c\nd'))
    assert result.returncode == 0, result.stderr
    record = json.loads(path.read_text())
    assert record["user"] == sudo_user
    assert record["consenting_user_named"] is True
    assert record["user_source"] == "SUDO_USER"
    assert record["version"] == '1.0"quoted'
    assert record["license_text"] == 'a"b\\c\nd'
    assert record["accepted_at"].endswith("Z")
    assert path.stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize("sudo_user", [None, ""])
def test_unnamed_user_is_the_effective_account(tmp_path, sudo_user):
    result, path = write_record(tmp_path, sudo_user)
    assert result.returncode == 0, result.stderr
    record = json.loads(path.read_text())
    expected = subprocess.check_output(["/usr/bin/id", "-un"], text=True).strip()
    assert record["user"] == expected
    assert record["consenting_user_named"] is False
    assert record["user_source"] == "effective_uid"


@pytest.mark.parametrize("preamble", ["id() { return 1; }\n", "python3() { return 1; }\n"])
def test_identity_or_encoder_failure_preserves_existing_record(tmp_path, preamble):
    path = tmp_path / "accepted.json"
    path.write_bytes(b"existing record\n")
    result, _ = write_record(tmp_path, preamble=preamble)
    assert result.returncode != 0
    assert path.read_bytes() == b"existing record\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["accepted.json"]


@pytest.mark.parametrize("extra", [("user", "forged"), ("extra",), ("x", "1", "x", "2")])
def test_invalid_metadata_cannot_override_identity_or_publish(tmp_path, extra):
    result, path = write_record(tmp_path, "alice", extra)
    assert result.returncode != 0
    assert not path.exists()


def test_existing_record_symlink_is_replaced_without_touching_referent(tmp_path):
    outside = tmp_path / "unchanged"
    outside.write_bytes(b"keep\n")
    (tmp_path / "accepted.json").symlink_to(outside)
    result, path = write_record(tmp_path, "alice")
    assert result.returncode == 0, result.stderr
    assert not path.is_symlink()
    assert outside.read_bytes() == b"keep\n"
    assert json.loads(path.read_text())["user"] == "alice"
