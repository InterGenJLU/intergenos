# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A check that could not be carried out must not publish "up to date".

`pkm check-updates` computes the desktop notifier's advisory. When the
repository cache cannot be used it consulted an empty index, found nothing
upgradable, printed "Everything is up to date.", WROTE count 0 into
/var/lib/pkm/available-updates.json and exited 0. Measured 2026-09-22 on a
cache directory made read-only: exactly that, with a package installed and
an index that was never readable. A machine with pending updates then tells
its user, through the top bar and the message of the day, that there are
none — a failure reported as a clean result.

The end-of-transaction refresh of the SAME advisory already refuses to do
this: `refresh_available_updates_after_transaction` says in as many words
that recomputing with no synced index "would report zero upgradable and
CLOBBER a real advisory", so it leaves the last-good file alone and states
why. This test holds the scheduled check to the rule its sibling already
keeps.

What the scheduled check must do when it could not consult the index:
  - say so, naming the cache path and the reason (a permission, a missing
    index), not a package count;
  - exit non-zero, so the timer's Restart=on-failure and any scripted
    caller see it;
  - write NOTHING — no fresh file, no clobbered previous one, no .tmp
    sibling left behind.

The unwritable-cache condition is built by pointing the cache constants at
a real read-only directory; `--root` cannot be used for it, because
check-updates refuses --root outright (it reports what the RUNNING system
could upgrade). Root bypasses directory permissions, so the permission leg
skips as root and says why instead of passing vacuously.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest

from pkm import cli
from pkm import repo as repo_module
from pkm.database import PackageDB

RUNNING_AS_ROOT = (os.geteuid() == 0)
ROOT_SKIP = ("root bypasses the directory permission this leg relies on; the "
             "condition under test is a cache directory the invoking user "
             "may not write")


def _args(quiet=False):
    return argparse.Namespace(quiet=quiet)


@pytest.fixture
def machine(tmp_path):
    """One installed package, an advisory file already holding a real count."""
    db_path = tmp_path / "pkm.db"
    with PackageDB(db_path) as db:
        db.add_installed("firefox", "138.0", release=1, tier="desktop")
    out = tmp_path / "state" / "available-updates.json"
    out.parent.mkdir()
    out.write_text(json.dumps({
        "timestamp": "2026-09-21T00:00:00Z", "checked_at": 1, "count": 1,
        "packages": [{"name": "firefox", "installed_version": "138.0",
                      "installed_release": 1, "remote_version": "139.0",
                      "remote_release": 1}],
        "skipped": [], "skipped_count": 0,
    }, indent=2, sort_keys=True))
    return db_path, out


def _point_cache_at(monkeypatch, cache: Path):
    monkeypatch.setattr(repo_module, "REPO_CACHE_DIR", cache)
    monkeypatch.setattr(repo_module, "REPO_DB_CACHE", cache / "db")
    monkeypatch.setattr(repo_module, "REPO_PKG_CACHE", cache / "packages")
    monkeypatch.setattr(repo_module, "REPO_ROLLBACK_DIR", cache / "rollback")


@pytest.mark.skipif(RUNNING_AS_ROOT, reason=ROOT_SKIP)
def test_unwritable_cache_is_named_and_nothing_is_published(
        machine, tmp_path, monkeypatch, capsys):
    db_path, out = machine
    before = out.read_bytes()
    cache = tmp_path / "cache"
    cache.mkdir()
    cache.chmod(0o555)
    _point_cache_at(monkeypatch, cache)
    monkeypatch.setattr(cli, "repo_manager", lambda: repo_module.RepoManager())

    with PackageDB(db_path, read_only=True) as db:
        rc = cli.cmd_check_updates(db, _args(), output_path=out)
    captured = capsys.readouterr()
    text = captured.out + captured.err

    assert rc, "a check that could not run must not exit 0"
    assert "Everything is up to date" not in text
    assert str(cache) in text, "the message does not name the cache path"
    assert ("permission" in text.lower() or "denied" in text.lower()), (
        "the message does not say what stopped it")
    # Nothing published, nothing clobbered, nothing half-written.
    assert out.read_bytes() == before
    assert not out.with_name(out.name + ".tmp").exists()


@pytest.mark.skipif(RUNNING_AS_ROOT, reason=ROOT_SKIP)
def test_unwritable_cache_does_not_create_an_advisory_that_was_absent(
        tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "pkm.db"
    with PackageDB(db_path) as db:
        db.add_installed("firefox", "138.0", release=1, tier="desktop")
    out = tmp_path / "state" / "available-updates.json"
    cache = tmp_path / "cache"
    cache.mkdir()
    cache.chmod(0o555)
    _point_cache_at(monkeypatch, cache)
    monkeypatch.setattr(cli, "repo_manager", lambda: repo_module.RepoManager())

    with PackageDB(db_path, read_only=True) as db:
        rc = cli.cmd_check_updates(db, _args(quiet=True), output_path=out)
    capsys.readouterr()
    assert rc
    assert not out.exists(), (
        "a zero the check never established was written as a fresh advisory")


def test_no_synced_index_is_refused_the_same_way(
        machine, tmp_path, monkeypatch, capsys):
    """A usable but empty cache is the same unchecked zero."""
    db_path, out = machine
    before = out.read_bytes()
    cache = tmp_path / "cache"
    _point_cache_at(monkeypatch, cache)
    monkeypatch.setattr(cli, "repo_manager", lambda: repo_module.RepoManager())

    with PackageDB(db_path, read_only=True) as db:
        rc = cli.cmd_check_updates(db, _args(), output_path=out)
    captured = capsys.readouterr()
    text = captured.out + captured.err

    assert rc
    assert "Everything is up to date" not in text
    assert "pkm update" in text, "the message does not say how to fix it"
    assert out.read_bytes() == before


def test_a_working_cache_still_publishes(machine, tmp_path, monkeypatch,
                                         capsys):
    """The guard must not refuse a check that genuinely ran."""
    db_path, out = machine

    class _Repo:
        cache_ready = True
        cache_error = None

        def has_synced_index(self):
            return True

        def get_package(self, name):
            return {"name": "firefox", "version": "140.0", "release": 1}

    monkeypatch.setattr(cli, "repo_manager", lambda: _Repo())
    with PackageDB(db_path, read_only=True) as db:
        rc = cli.cmd_check_updates(db, _args(quiet=True), output_path=out)
    capsys.readouterr()
    assert not rc
    data = json.loads(out.read_text())
    assert data["count"] == 1
    assert data["packages"][0]["remote_version"] == "140.0"


def test_a_genuine_zero_is_still_published(machine, tmp_path, monkeypatch,
                                           capsys):
    """Up to date, established against a readable index, still publishes."""
    db_path, out = machine

    class _Repo:
        cache_ready = True
        cache_error = None

        def has_synced_index(self):
            return True

        def get_package(self, name):
            return {"name": "firefox", "version": "138.0", "release": 1}

    monkeypatch.setattr(cli, "repo_manager", lambda: _Repo())
    with PackageDB(db_path, read_only=True) as db:
        rc = cli.cmd_check_updates(db, _args(), output_path=out)
    text = capsys.readouterr().out
    assert not rc
    assert "Everything is up to date." in text
    assert json.loads(out.read_text())["count"] == 0


@pytest.mark.skipif(RUNNING_AS_ROOT, reason=ROOT_SKIP)
def test_the_manager_records_why_the_cache_is_unusable(tmp_path, monkeypatch):
    """The seam itself: `cache_ready` False says nothing about the cause, and
    a message a person reads needs the cause."""
    cache = tmp_path / "cache"
    cache.mkdir()
    cache.chmod(0o555)
    _point_cache_at(monkeypatch, cache)
    mgr = repo_module.RepoManager()
    assert mgr.cache_ready is False
    assert mgr.cache_error is not None
    path, err = mgr.cache_error
    assert str(cache) in str(path)
    assert isinstance(err, OSError)


def test_a_usable_cache_records_no_error(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    _point_cache_at(monkeypatch, cache)
    mgr = repo_module.RepoManager()
    assert mgr.cache_ready is True
    assert mgr.cache_error is None
