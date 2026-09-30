# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A directory request stands for the stored paths beneath it; an absent path for nothing."""

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
    """A directory request stands for its stored contents; an absent path stands
    for nothing. The two must not read alike."""
    backend, version, directory, document, *_ = saved_point
    requested = str(directory) + ("/" if trailing_slash else "")
    absent = str(directory.parent / "documents-other")
    plan = backend.restore_plan("restore-point", version, [requested, absent])
    stored_action, absent_action = plan["actions"]
    assert stored_action["path"] == str(document)
    assert stored_action["action"] == "restore"
    assert stored_action["requested_as"] == requested
    assert absent_action["action"] == "skip"
    assert absent_action["reason"] == "not in this version"
    assert document.read_text() == "saved content\n"


def test_stored_descendants_identify_a_directory_that_no_longer_exists(saved_point):
    """The version's own entries decide what a directory request means, not what
    exists at that path now."""
    backend, version, directory, document, *_ = saved_point
    directory.rename(directory.with_name("moved"))
    actions = backend.restore_plan("restore-point", version, [str(directory)])["actions"]
    assert [a["path"] for a in actions] == [str(document)]
    assert actions[0]["action"] == "restore"
    assert not directory.exists()


@pytest.mark.parametrize("live_type", ["directory", "file", "symlink", "nonexistent"])


def test_uncaptured_paths_are_not_in_this_version_for_plan_and_apply(
        saved_point, monkeypatch, live_type):
    backend, version, directory, document, *_ = saved_point
    absent = directory.parent / "uncaptured"
    if live_type == "directory":
        absent.mkdir()
    elif live_type == "file":
        absent.write_text("uncaptured content\n")
    elif live_type == "symlink":
        absent.symlink_to(directory, target_is_directory=True)
    document.write_text("current content\n")
    # Skipped requests make no metadata writes and must not start a service.
    monkeypatch.setattr(escalate, "has_cap_chown", lambda: True)
    monkeypatch.setattr(escalate, "run_restore_via_unit",
                        lambda *args, **kwargs: pytest.fail("unexpected service escalation"))
    action = backend.restore_plan("restore-point", version, [str(absent)])["actions"][0]
    result = backend.restore_apply("restore-point", version, [str(absent)])["results"][0]
    assert action["action"] == "skip"
    assert action["reason"] == "not in this version"
    assert result["ok"] is False
    assert result["reason"] == action["reason"]
    assert result["path"] == action["path"] == str(absent)
    assert document.read_text() == "current content\n"
    if live_type == "file":
        assert absent.read_text() == "uncaptured content\n"
    elif live_type == "directory":
        assert absent.is_dir()
    elif live_type == "symlink":
        assert absent.is_symlink()
        assert absent.readlink() == directory
    else:
        assert not absent.exists()


def test_recorded_directory_preview_names_the_paths_that_ride_with_it(saved_point):
    """The real command line, driven as a person drives it: the preview names the
    directory, says how many paths ride with it, and names each one."""
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
    out = result.stdout
    assert "1 path(s) beneath it" in out
    assert str(document) in out
    assert "metadata only" not in out.lower()
    assert "Dry run" in out
    assert document.read_text() == "saved content\n"


def test_apply_restores_the_children_the_plan_named(saved_point, monkeypatch):
    """Plan and apply agree path for path, and the child is really written."""
    backend, version, directory, document, *_ = saved_point
    document.write_text("current content\n")
    monkeypatch.setattr(escalate, "has_cap_chown", lambda: True)
    monkeypatch.setattr(escalate, "run_restore_via_unit",
                        lambda *a, **k: pytest.fail("restore escalated instead of running"))
    plan = backend.restore_plan("restore-point", version, [str(directory)])
    result = backend.restore_apply("restore-point", version, [str(directory)])
    assert [a["path"] for a in plan["actions"]] == [r["path"] for r in result["results"]]
    assert all(r["ok"] for r in result["results"]), result
    assert document.read_text() == "saved content\n"


def test_recorded_directory_restores_its_metadata_and_its_children(saved_point, monkeypatch):
    """The directory's recorded mode still lands — and now its contents land with
    it, in one request."""
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
    assert all(r["ok"] for r in result["results"]), result
    assert directory.stat().st_mode & 0o777 == 0o750
    assert document.read_text() == "saved content\n"


@pytest.mark.parametrize("json_mode", [False, True])
def test_real_cli_preview_explains_directory_and_absent_arguments(saved_point, json_mode):
    """The preview distinguishes a directory that stands for stored paths from a
    path the version does not hold, in plain text and in JSON."""
    _, version, directory, document, store, conf = saved_point
    entry = Path(__file__).resolve().parents[2] / "assets/intergenos-backup/chronicle-cli"
    absent = directory.parent / "missing"
    result = subprocess.run(
        [sys.executable, str(entry), "--local-root", str(store), "--config", str(conf),
         "--socket", str(store / "absent.sock"), "restore", "restore-point", version,
         str(directory), str(absent), "--dry-run", *(["--json"] if json_mode else [])],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "not in this version" in result.stdout
    assert "not restored recursively" not in result.stdout.lower()
    if json_mode:
        actions = json.loads(result.stdout)["actions"]
        stored_action, absent_action = actions
        assert stored_action["path"] == str(document)
        assert stored_action["action"] == "restore"
        assert stored_action["requested_as"] == str(directory)
        assert absent_action["action"] == "skip"
    else:
        assert str(document) in result.stdout


@pytest.mark.parametrize("json_mode", [False, True])


def test_real_cli_preserves_nonblank_path_bytes_and_quotes_skips(saved_point, json_mode):
    backend, _, directory, _, store, conf = saved_point
    document = directory / " note with spaces\t "
    document.write_text("saved content\n")
    version = backend.capture("restore-point", scope={
        "paths": [str(document)], "packages": ["sample"],
    })["version_id"]
    absent = directory.parent / " missing 'name'\\part\t\n "
    entry = Path(__file__).resolve().parents[2] / "assets/intergenos-backup/chronicle-cli"
    result = subprocess.run(
        [sys.executable, str(entry), "--local-root", str(store), "--config", str(conf),
         "--socket", str(store / "absent.sock"), "restore", "restore-point", version,
         str(document), str(absent), "--dry-run", *(["--json"] if json_mode else [])],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    if json_mode:
        stored_action, absent_action = json.loads(result.stdout)["actions"]
        assert stored_action["action"] == "restore"
        assert stored_action["path"] == str(document)
        assert absent_action["action"] == "skip"
        assert absent_action["path"] == str(absent)
    else:
        assert f"OVERWRITE (with confirmation) {str(document)!r}" in result.stdout
        assert f"  SKIP {str(absent)!r} — not in this version" in result.stdout.splitlines()
    assert document.read_text() == "saved content\n"
