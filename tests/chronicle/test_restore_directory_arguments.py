# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Directory requests are distinguished from paths absent from a version."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from chronicle import config, engine, escalate


@pytest.fixture
def saved_point(tmp_path):
    directory = tmp_path / "documents"
    directory.mkdir()
    document = directory / "note"
    document.write_text("saved content\n")
    store = tmp_path / "store"
    conf = tmp_path / "chronicle.conf"
    conf.write_text("# Isolated restore test.\n")
    backend = engine.Engine(local_root=store, config=config.Config())
    version = backend.capture(
        "restore-point", scope={"paths": [str(document)], "packages": ["sample"]},
        reason="restore argument test",
    )["version_id"]
    return backend, version, directory, document, store, conf


@pytest.mark.parametrize("trailing_slash", [False, True])
def test_directory_and_absent_path_have_distinct_plans(saved_point, trailing_slash):
    backend, version, directory, document, *_ = saved_point
    requested = str(directory) + ("/" if trailing_slash else "")
    absent = str(directory.parent / "documents-other")
    plan = backend.restore_plan("restore-point", version, [requested, absent])
    directory_action, absent_action = plan["actions"]
    assert directory_action["action"] == "skip"
    assert "directory" in directory_action["reason"].lower()
    assert "individual" in directory_action["reason"].lower()
    assert absent_action["reason"] == "not in this version"
    assert document.read_text() == "saved content\n"


def test_stored_descendants_identify_a_directory_that_no_longer_exists(saved_point):
    backend, version, directory, _, *_ = saved_point
    directory.rename(directory.with_name("moved"))
    action = backend.restore_plan("restore-point", version, [str(directory)])["actions"][0]
    assert "directory" in action["reason"].lower()
    assert not directory.exists()


def test_uncaptured_live_directory_is_reported_as_a_directory(saved_point):
    backend, version, directory, _, *_ = saved_point
    empty = directory.parent / "empty"
    empty.mkdir()
    action = backend.restore_plan("restore-point", version, [str(empty)])["actions"][0]
    assert "directory" in action["reason"].lower()


def test_missing_symlink_to_directory_remains_an_absent_path(saved_point):
    backend, version, directory, _, *_ = saved_point
    link = directory.parent / "link"
    link.symlink_to(directory, target_is_directory=True)
    action = backend.restore_plan("restore-point", version, [str(link)])["actions"][0]
    assert action["reason"] == "not in this version"


def test_recorded_directory_preview_names_metadata_only(saved_point):
    backend, _, directory, document, store, conf = saved_point
    version = backend.capture("restore-point", scope={
        "paths": [str(directory), str(document)], "packages": ["sample"],
    })["version_id"]
    entry = Path(__file__).resolve().parents[2] / "assets/intergenos-backup/chronicle-cli"
    result = subprocess.run(
        [sys.executable, str(entry), "--local-root", str(store), "--config", str(conf),
         "--socket", str(store / "absent.sock"), "restore", "restore-point", version,
         str(directory), "--dry-run"], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "directory metadata only" in result.stdout.lower()
    assert document.read_text() == "saved content\n"


def test_apply_reports_the_same_directory_limitation_without_restoring_children(
        saved_point, monkeypatch):
    backend, version, directory, document, *_ = saved_point
    document.write_text("current content\n")
    # Isolate the service boundary; no ownership changes occur for a skipped path.
    monkeypatch.setattr(escalate, "has_cap_chown", lambda: True)
    plan = backend.restore_plan("restore-point", version, [str(directory)])
    result = backend.restore_apply("restore-point", version, [str(directory)])
    assert result["results"][0]["ok"] is False
    assert "directory" in result["results"][0]["reason"].lower()
    assert result["results"][0]["reason"] == plan["actions"][0]["reason"]
    assert document.read_text() == "current content\n"


def test_recorded_directory_restores_metadata_without_restoring_children(saved_point, monkeypatch):
    backend, _, directory, document, *_ = saved_point
    directory.chmod(0o750)
    version = backend.capture("restore-point", scope={
        "paths": [str(directory), str(document)], "packages": ["sample"],
    })["version_id"]
    directory.chmod(0o700)
    document.write_text("current content\n")
    # Exercise native metadata writes as the invoking uid; no service is started.
    monkeypatch.setattr(escalate, "has_cap_chown", lambda: True)
    result = backend.restore_apply("restore-point", version, [str(directory)])
    assert result["results"][0]["ok"] is True, result
    assert directory.stat().st_mode & 0o777 == 0o750
    assert document.read_text() == "current content\n"


@pytest.mark.parametrize("json_mode", [False, True])
def test_real_cli_preview_explains_directory_and_absent_arguments(saved_point, json_mode):
    _, version, directory, _, store, conf = saved_point
    entry = Path(__file__).resolve().parents[2] / "assets/intergenos-backup/chronicle-cli"
    absent = directory.parent / "missing"
    result = subprocess.run(
        [sys.executable, str(entry), "--local-root", str(store), "--config", str(conf),
         "--socket", str(store / "absent.sock"), "restore", "restore-point", version,
         str(directory), str(absent), "--dry-run", *(["--json"] if json_mode else [])],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "directory contents are not restored recursively" in result.stdout.lower()
    assert "name the individual stored paths" in result.stdout.lower()
    assert "not in this version" in result.stdout
    if json_mode:
        actions = json.loads(result.stdout)["actions"]
        assert actions[0]["reason"] != actions[1]["reason"]
