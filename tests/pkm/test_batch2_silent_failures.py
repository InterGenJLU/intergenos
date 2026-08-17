# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Regression gates for the pkm Batch-2 silent-failure fixes.

  PKM-A09  cmd_update surfaces packages it could NOT version-compare
           instead of folding them into a clean "all up to date".
  PKM-A10  cmd_update surfaces installed packages absent from every repo
           (outside the update horizon) instead of silently skipping them.
  PKM-A12  query_active_services skips a single flaky/slow unit and keeps
           scanning, instead of aborting the whole scan to [] (which falsely
           reported "no restart needed" while daemons had been upgraded).
"""
import re
import subprocess
import sys
from unittest import mock

import pytest

from pkm.database import PackageDB
from pkm.cli import cmd_update
import pkm.cli as cli
import pkm.services as svc


# --------------------------- PKM-A12 ---------------------------------------

def _fake_run_factory(behavior):
    def _run(cmd, **kw):
        unit = cmd[-1]
        b = behavior.get(unit, "inactive")
        if b == "timeout":
            raise subprocess.TimeoutExpired(cmd, 10)
        if b == "oserror":
            raise OSError("boom")
        return mock.Mock(returncode=0 if b == "active" else 3)
    return _run


def test_a12_flaky_unit_does_not_mask_other_active_services(monkeypatch, capsys):
    monkeypatch.setattr(svc, "_TRACE_AVAILABLE", False)
    monkeypatch.setattr(svc.subprocess, "run", _fake_run_factory(
        {"a.service": "active", "b.service": "timeout", "c.service": "active"}))
    active = svc.query_active_services(["a.service", "b.service", "c.service"])
    assert active == ["a.service", "c.service"]          # NOT aborted to []
    assert "b.service" in capsys.readouterr().err        # the skip is surfaced


def test_a12_oserror_unit_skipped_not_aborted(monkeypatch):
    monkeypatch.setattr(svc, "_TRACE_AVAILABLE", False)
    monkeypatch.setattr(svc.subprocess, "run", _fake_run_factory(
        {"a.service": "active", "b.service": "oserror"}))
    assert svc.query_active_services(["a.service", "b.service"]) == ["a.service"]


def test_a12_systemctl_absent_returns_empty(monkeypatch):
    monkeypatch.setattr(svc, "_TRACE_AVAILABLE", False)

    def _missing(cmd, **kw):
        raise FileNotFoundError("systemctl")
    monkeypatch.setattr(svc.subprocess, "run", _missing)
    assert svc.query_active_services(["a.service"]) == []


# ------------------------- PKM-A09 / PKM-A10 -------------------------------

class _FakeRepo:
    def __init__(self, remotes):
        self._r = remotes

    def sync(self, reporter=None):
        return []                      # no repo failed

    def get_package(self, name):
        return self._r.get(name)


def _args(**kw):
    return mock.Mock(quiet=False, verbose=False, **kw)


def test_cmd_update_surfaces_not_in_repo_and_unevaluable(tmp_path, capsys):
    db = PackageDB(tmp_path / "t.db")
    try:
        db.add_installed(name="firefox", version="138.0", release=1)
        db.add_installed(name="orphan-pkg", version="1.0", release=1)
        db.add_installed(name="weird-pkg", version="1.0", release=1)
        remotes = {
            "firefox": {"name": "firefox", "version": "139.0", "release": 1},
            # orphan-pkg absent -> PKM-A10 not_in_repo
            "weird-pkg": {"name": "weird-pkg", "version": "", "release": 1},  # empty -> PKM-A09
        }
        with mock.patch("pkm.cli.RepoManager", return_value=_FakeRepo(remotes)):
            cmd_update(db, _args())
        cap = capsys.readouterr()
        # The Reporter wraps prose with hanging indents, so collapse whitespace
        # before substring-matching (else a phrase split across a wrap boundary
        # would spuriously fail).
        text = re.sub(r"\s+", " ", cap.out + cap.err).lower()
        assert "orphan-pkg" in text                 # A10 surfaced
        assert "will not receive updates" in text
        assert "weird-pkg" in text                  # A09 surfaced
        assert "could not be version-compared" in text
        assert "can be upgraded" in text            # firefox still flagged upgradable
    finally:
        db.close()


# ----------------------------- PKM-A07 -------------------------------------

def _prep_db(tmp_path):
    p = tmp_path / "t.db"
    PackageDB(str(p)).close()   # create the schema so main() can open it
    return str(p)


def test_a07_dispatch_propagates_nonzero_exit(tmp_path, monkeypatch):
    # A handler returning a nonzero code MUST become the process exit code; the
    # old bare-statement dispatch discarded it and the process exited 0.
    monkeypatch.setattr(cli, "cmd_info", lambda db, args: 7)
    monkeypatch.setattr(sys, "argv",
                        ["pkm", "--db", _prep_db(tmp_path), "info", "anything"])
    with pytest.raises(SystemExit) as ei:
        cli.main()
    assert ei.value.code == 7


def test_a07_dispatch_zero_exit_when_handler_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "cmd_info", lambda db, args: None)
    monkeypatch.setattr(sys, "argv",
                        ["pkm", "--db", _prep_db(tmp_path), "info", "anything"])
    try:
        cli.main()
    except SystemExit as e:
        assert (e.code or 0) == 0
