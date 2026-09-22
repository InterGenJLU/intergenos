# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Verifying a package that is not installed is not a pass.

`pkm verify <name>` printed "Package 'X' is not installed" and exited 0.
A caller that gates on the exit status therefore treated an ABSENT package
as a verified one — the silent failure this test closes. The measured
consumer is installer/smoke/ge-eval-stage.sh, which runs
`pkm verify "$p" || fail ...` over the gaming meta's declared dependency
set: before this change a member that never installed passed that gate.

The three states a caller must be able to tell apart keep three distinct
statuses, and the absent state gets its own:

  present and clean      -> 0
  present but corrupt    -> 1   (files missing or modified)
  usage error            -> 2   (neither a package name nor --all)
  could not be checked   -> 3   (nothing failed; a check could not run)
  NOT INSTALLED          -> 4   (this change)

4 is chosen because 2 is already taken by the usage error that
installer/smoke/checks/pkm.sh reads by number (its H-006 note), and a new
code that collided with it would make a missing --all and an absent package
indistinguishable to that consumer.

The printed message is unchanged: this is a status fix, not a wording one.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pkm import cli
from pkm.database import PackageDB

REPO_ROOT = Path(__file__).resolve().parents[2]


def _args(package=None, verify_all=False, mode="strict"):
    return SimpleNamespace(package=package, verify_all=verify_all,
                           verify_mode=mode, verify_detail=False)


@pytest.fixture
def verify_root(tmp_path):
    """An install root holding one clean package and nothing else."""
    root = tmp_path / "root"
    (root / "usr" / "bin").mkdir(parents=True)
    payload = root / "usr" / "bin" / "example"
    payload.write_text("#!/bin/sh\nexit 0\n")
    db_path = tmp_path / "pkm.db"
    with PackageDB(db_path, root=str(root)) as db:
        pkg_id = db.add_installed("example", "1.0", release=1, tier="core")
        db.add_files(pkg_id, ["usr/bin/example"])
    return root, db_path


def test_absent_package_reports_a_status_of_its_own(verify_root, capsys):
    root, db_path = verify_root
    with PackageDB(db_path, root=str(root), read_only=True) as db:
        rc = cli.cmd_verify(db, _args(package="not-installed-here"))
    out = capsys.readouterr().out
    assert rc == cli.VERIFY_EXIT_NOT_INSTALLED
    assert rc != 0
    # The wording a person reads is unchanged by this fix.
    assert "Package 'not-installed-here' is not installed" in out


def test_absent_status_is_distinct_from_every_other_verify_status(
        verify_root, capsys):
    """A caller can tell absent from corrupt, from unusable, from misuse."""
    root, db_path = verify_root
    with PackageDB(db_path, root=str(root), read_only=True) as db:
        absent = cli.cmd_verify(db, _args(package="not-installed-here"))
    capsys.readouterr()
    # An integer, because a caller branches on it; and none of the four codes
    # verify already spends. `None` is what the pre-fix code returned and is
    # exactly what this assertion must not accept.
    assert isinstance(absent, int)
    assert absent not in (0, 1, 2, 3)


def test_installed_and_clean_still_exits_zero(verify_root, capsys):
    root, db_path = verify_root
    with PackageDB(db_path, root=str(root), read_only=True) as db:
        rc = cli.cmd_verify(db, _args(package="example"))
    out = capsys.readouterr().out
    assert not rc
    assert "example: ok" in out


def test_installed_but_modified_still_exits_one(verify_root, capsys):
    root, db_path = verify_root
    (root / "usr" / "bin" / "example").unlink()
    with PackageDB(db_path, root=str(root), read_only=True) as db:
        with pytest.raises(SystemExit) as exc:
            cli.cmd_verify(db, _args(package="example"))
    capsys.readouterr()
    assert exc.value.code == 1


def test_verify_all_is_untouched_by_the_absent_status(verify_root, capsys):
    """--all verifies what IS installed; it names no package, so it can
    never reach the absent path."""
    root, db_path = verify_root
    with PackageDB(db_path, root=str(root), read_only=True) as db:
        rc = cli.cmd_verify(db, _args(verify_all=True))
    out = capsys.readouterr().out
    assert not rc
    assert "1 ok" in out


def test_no_package_and_no_all_still_exits_two(verify_root, capsys):
    root, db_path = verify_root
    with PackageDB(db_path, root=str(root), read_only=True) as db:
        with pytest.raises(SystemExit) as exc:
            cli.cmd_verify(db, _args())
    capsys.readouterr()
    assert exc.value.code == 2


def test_whole_process_exits_four_for_an_absent_package(verify_root, tmp_path):
    """Through the real entry point: the code a shell caller actually sees."""
    root, db_path = verify_root
    env = os.environ.copy()
    env["IGOS_TRACE_ROOT"] = str(tmp_path / "trace")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pkm", "--root", str(root), "--db", str(db_path),
         "verify", "not-installed-here"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 4, (result.returncode, result.stdout,
                                    result.stderr)
    assert "Traceback" not in result.stderr
    assert "is not installed" in result.stdout


def test_whole_process_exits_zero_for_an_installed_clean_package(
        verify_root, tmp_path):
    root, db_path = verify_root
    env = os.environ.copy()
    env["IGOS_TRACE_ROOT"] = str(tmp_path / "trace")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pkm", "--root", str(root), "--db", str(db_path),
         "verify", "example"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, (result.returncode, result.stdout,
                                    result.stderr)


def test_the_man_page_states_the_absent_status():
    """A status a caller is expected to branch on is documented."""
    man = (REPO_ROOT / "packages" / "core" / "pkm" / "pkm.1").read_text()
    verify_entry = man.split('.BR verify " [\\fIPACKAGE\\fR]')[1].split(".TP")[0]
    assert "not installed" in verify_entry
    assert "4" in verify_entry
