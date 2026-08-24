# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Unit coverage for owner-only per-user state and the one-time migration.

The fresh-HOME gate in ``test_state_file_permissions.py`` proves the whole
composition on a new home directory. This file covers the two things that gate
cannot reach:

* the migration pass, which is about an home directory that ALREADY exists and
  already holds world-readable state;
* the create sites whose real entry point is not drivable from a headless
  child — the console history (its writer is the interactive REPL loop), the
  MCP schema pins and audit fallback, and the licence-acceptance records.

It also pins the specific recreate hole measured against the release: a Glass
trace that is removed and re-created by the next append.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path

import pytest

from intergen import private_state
from intergen.private_state import (
    DIR_MODE,
    FILE_MODE,
    PrivateRotatingFileHandler,
    harden_user_state,
    private_dir,
    private_open,
    private_touch,
    private_write_text,
)


def mode_of(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """A throwaway home with every XDG base pointed inside it."""
    h = tmp_path / "home"
    # 0755, not 0700: a tight home would shield every mode under it and the
    # assertions would stop measuring what this module actually sets.
    h.mkdir(mode=0o755)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("XDG_STATE_HOME", str(h / ".local" / "state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(h / ".local" / "share"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(h / ".config"))
    return h


# ── creation helpers ──────────────────────────────────────────────────────


def test_private_dir_creates_owner_only(home):
    created = private_dir(private_state.data_dir_path())
    assert mode_of(created) == DIR_MODE


def test_private_dir_tightens_a_pre_existing_world_listable_directory(home):
    target = private_state.state_dir_path()
    target.mkdir(parents=True)
    os.chmod(target, 0o755)
    assert mode_of(target) == 0o755
    private_dir(target)
    assert mode_of(target) == DIR_MODE


def test_private_dir_tightens_owned_ancestors_not_just_the_leaf(home):
    """``parents=True`` creates intermediates at the platform default.

    A sessions directory at 0700 under an intergen directory at 0755 is still
    listable by every other account, so the ancestor has to move too.
    """
    sessions = private_state.data_dir_path() / "sessions"
    private_dir(sessions)
    assert mode_of(sessions) == DIR_MODE
    assert mode_of(private_state.data_dir_path()) == DIR_MODE


def test_private_dir_leaves_shared_xdg_parents_alone(home):
    """~/.local/share belongs to every application, not to this one."""
    private_dir(private_state.data_dir_path())
    shared = home / ".local" / "share"
    assert mode_of(shared) != DIR_MODE, (
        "a shared XDG parent was tightened; that is not ours to change"
    )


def test_private_open_creates_owner_only_under_a_loose_umask(home):
    previous = os.umask(0o022)
    try:
        path = private_state.state_dir_path() / "probe.jsonl"
        private_dir(path.parent)
        with private_open(path, "a") as handle:
            handle.write("{}\n")
        assert mode_of(path) == FILE_MODE
    finally:
        os.umask(previous)


def test_private_open_tightens_a_pre_existing_world_readable_file(home):
    path = private_state.state_dir_path() / "legacy.jsonl"
    private_dir(path.parent)
    path.write_text("old\n")
    os.chmod(path, 0o644)
    with private_open(path, "a") as handle:
        handle.write("new\n")
    assert mode_of(path) == FILE_MODE
    assert path.read_text() == "old\nnew\n", "tightening must not lose content"


def test_private_touch_creates_owner_only_and_is_idempotent(home):
    path = private_state.data_dir_path() / "store.db"
    private_touch(path)
    assert mode_of(path) == FILE_MODE
    path.write_text("payload")
    private_touch(path)
    assert mode_of(path) == FILE_MODE
    assert path.read_text() == "payload", "touch must not truncate"


def test_private_write_text_replaces_content_owner_only(home):
    path = private_state.config_dir_path() / "token"
    private_dir(path.parent)
    private_write_text(path, "first")
    private_write_text(path, "second")
    assert mode_of(path) == FILE_MODE
    assert path.read_text() == "second"


def test_rotating_handler_keeps_every_rollover_owner_only(home):
    """The stock handler opens each fresh file through plain ``open``."""
    path = private_state.state_dir_path() / "rotating.log"
    private_dir(path.parent)
    handler = PrivateRotatingFileHandler(path, maxBytes=200, backupCount=3)
    logger = logging.getLogger("intergen.tests.rotation")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        for i in range(200):
            logger.info("rotation probe line %03d padded out to force a roll", i)
    finally:
        logger.removeHandler(handler)
        handler.close()

    rolled = sorted(path.parent.glob("rotating.log*"))
    assert len(rolled) > 1, "the log never rotated, so rollovers went untested"
    wrong = {p.name: f"{mode_of(p):04o}" for p in rolled if mode_of(p) != FILE_MODE}
    assert not wrong, f"rotated log files not owner-only: {wrong}"


# ── the measured recreate hole ────────────────────────────────────────────


def test_glass_trace_stays_owner_only_after_being_removed(home):
    """Remove the trace, emit once, and it must come back owner-only.

    Measured against the release: the file was created 0600, then a plain
    append re-created it 0644 while still carrying prompt, model and delivered
    bytes. Any external rotation or removal reopened that hole with no signal.
    """
    from intergen import glass

    previous = os.umask(0o022)
    try:
        logger_obj = glass.GlassLogger(str(private_state.state_dir_path()))
        logger_obj.emit("gate", "first", detail={"probe": "one"})
        assert logger_obj._log_file is not None
        trace = Path(logger_obj._log_file)
        assert mode_of(trace) == FILE_MODE

        trace.unlink()
        logger_obj.emit("gate", "second", detail={"probe": "two"})

        assert trace.exists(), "the trace did not come back at all"
        assert mode_of(trace) == FILE_MODE, (
            "a removed trace was re-created world-readable by the next append"
        )
        assert json.loads(trace.read_text().splitlines()[0])["event"] == "second"
    finally:
        os.umask(previous)


def test_session_transcript_and_its_temp_file_are_owner_only(home):
    """The temp file carries the transcript, and rename preserves its mode."""
    from intergen import session_manager

    previous = os.umask(0o022)
    try:
        directory = private_state.data_dir_path() / "sessions"
        manager = session_manager.SessionManager(sessions_dir=directory)
        session = manager.create(source_interface="web")
        written = directory / f"{session['session_id']}.json"
        assert mode_of(written) == FILE_MODE
        assert mode_of(directory) == DIR_MODE
    finally:
        os.umask(previous)


def test_console_history_file_is_pre_created_owner_only(home):
    """The console history holds everything the user typed at the REPL.

    Its writer is prompt_toolkit's FileHistory, which appends through a plain
    open; pre-creating the file owner-only is what makes those appends
    owner-only, because an ordinary open never changes an existing mode.
    """
    previous = os.umask(0o022)
    try:
        history = private_state.data_dir_path() / "console_history"
        private_touch(history)
        assert mode_of(history) == FILE_MODE
        with open(history, "a", encoding="utf-8") as handle:
            handle.write("\n# what the user typed\n+probe\n")
        assert mode_of(history) == FILE_MODE
    finally:
        os.umask(previous)


# ── the one-time migration ────────────────────────────────────────────────


def _legacy_tree(home: Path) -> dict[str, Path]:
    """Build a home the way a release without owner-only state left it."""
    state = home / ".local" / "state" / "intergen"
    data = home / ".local" / "share" / "intergen"
    config = home / ".config" / "intergen"
    sessions = data / "sessions"
    for directory in (state, data, config, sessions):
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o755)

    made: dict[str, Path] = {}
    loose = {
        "memory": data / "memory.db",
        "session": sessions / "web_0123456789ab.json",
        "log": state / "intergen.log",
        # A backup logrotate renamed in, and a marker dropped by the shell
        # extension: nothing in this package creates either, and both are just
        # as readable by another account. The sweep is structural for exactly
        # this reason.
        "rotated": state / "intergen.log.1",
        "marker": data / "firstboot-animation-done",
    }
    for name, path in loose.items():
        path.write_text("payload\n")
        os.chmod(path, 0o644)
        made[name] = path

    already_tight = config / "web-token"
    already_tight.write_text("token\n")
    os.chmod(already_tight, 0o600)
    made["token"] = already_tight
    made.update(state=state, data=data, config=config, sessions=sessions)
    return made


def test_migration_tightens_an_existing_home(home):
    made = _legacy_tree(home)
    report = harden_user_state()

    assert report.clean, f"migration reported failures: {report.failures}"
    for key in ("state", "data", "config", "sessions"):
        assert mode_of(made[key]) == DIR_MODE, f"{key} still {mode_of(made[key]):04o}"
    for key in ("memory", "session", "log", "rotated", "marker", "token"):
        assert mode_of(made[key]) == FILE_MODE, f"{key} still {mode_of(made[key]):04o}"


def test_migration_reports_exactly_what_it_changed(home):
    made = _legacy_tree(home)
    report = harden_user_state()

    # The already-0600 token must NOT be counted as changed: the log line is
    # built from these lists, so an inflated count would be a false report.
    assert str(made["token"]) not in report.files_changed
    assert str(made["memory"]) in report.files_changed
    assert str(made["marker"]) in report.files_changed
    assert str(made["sessions"]) in report.dirs_changed
    assert report.changed == len(report.dirs_changed) + len(report.files_changed)


def test_migration_is_idempotent(home):
    _legacy_tree(home)
    first = harden_user_state()
    assert first.changed > 0
    second = harden_user_state()
    assert second.changed == 0, (
        f"a second pass still changed {second.dirs_changed + second.files_changed}"
    )
    assert second.clean


def test_migration_never_widens_a_stricter_mode(home):
    made = _legacy_tree(home)
    os.chmod(made["state"], 0o500)
    os.chmod(made["log"], 0o400)
    harden_user_state()
    assert mode_of(made["state"]) == 0o500, "a stricter directory was widened"
    assert mode_of(made["log"]) == 0o400, "a stricter file was widened"


def test_migration_skips_and_reports_symlinks(home):
    made = _legacy_tree(home)
    outside = home / "outside.txt"
    outside.write_text("not ours\n")
    os.chmod(outside, 0o644)
    link = made["data"] / "link-to-outside"
    link.symlink_to(outside)

    report = harden_user_state()

    assert str(link) in report.skipped_symlinks
    assert mode_of(outside) == 0o644, (
        "chmod followed a symlink and changed a file outside the tree"
    )


def test_migration_records_a_failure_instead_of_raising(home, monkeypatch):
    made = _legacy_tree(home)
    target = str(made["memory"])

    real_chmod = os.chmod

    def refusing_chmod(path, mode, *args, **kwargs):
        if str(path) == target:
            raise PermissionError(13, "Operation not permitted")
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(private_state.os, "chmod", refusing_chmod)
    report = harden_user_state()

    assert not report.clean
    assert any(target in entry for entry in report.failures)
    # The rest of the tree must still have been done.
    assert mode_of(made["session"]) == FILE_MODE


def test_startup_migration_names_failures_rather_than_reporting_success(
        home, monkeypatch, caplog):
    made = _legacy_tree(home)
    target = str(made["log"])
    real_chmod = os.chmod

    def refusing_chmod(path, mode, *args, **kwargs):
        if str(path) == target:
            raise PermissionError(13, "Operation not permitted")
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(private_state.os, "chmod", refusing_chmod)
    with caplog.at_level(logging.INFO, logger="intergen.private_state"):
        report = private_state.harden_user_state_at_startup()

    assert not report.clean
    text = caplog.text
    assert "could NOT be adjusted" in text, (
        "a partial migration must not be logged as a success"
    )
    assert target in text, "the failing path must be named in the log"


def test_startup_migration_on_a_clean_home_claims_nothing(home, caplog):
    private_dir(private_state.state_dir_path())
    with caplog.at_level(logging.INFO, logger="intergen.private_state"):
        report = private_state.harden_user_state_at_startup()
    assert report.clean and report.changed == 0
    assert "tightened" not in caplog.text, (
        "nothing changed, so nothing may be claimed"
    )


def test_migration_on_an_absent_home_tree_is_a_no_op(home):
    report = harden_user_state()
    assert report.clean
    assert report.changed == 0
    assert report.roots_present == []
