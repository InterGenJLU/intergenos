# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Package information keeps its output and reports installed state to callers."""
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from pkm import cli
from pkm.database import PackageDB


@pytest.fixture
def info_db(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    path = tmp_path / "pkm.db"
    with PackageDB(path, root=str(root)) as db:
        db.add_installed("example-installed", "1.0", release=2, tier="core")
    return root, path


@pytest.mark.parametrize("index_state", ["available", "missing", "unreadable"])
def test_uninstalled_info_returns_one_and_keeps_its_answer(
        info_db, monkeypatch, capsys, index_state):
    root, path = info_db
    manager = Mock()
    if index_state == "available":
        manager.get_package.return_value = {
            "name": "example-available", "version": "2.0", "release": 3,
            "tier": "extra", "description": "Example available package",
            "license": "MIT",
        }
    elif index_state == "missing":
        manager.get_package.return_value = None
    else:
        manager.get_package.side_effect = OSError("index unreadable")
    monkeypatch.setattr(cli, "repo_manager", lambda: manager)
    with PackageDB(path, root=str(root), read_only=True) as db:
        rc = cli.cmd_info(db, SimpleNamespace(package="example-available"))
    out = capsys.readouterr().out
    assert rc == 1
    assert "not installed" in out
    if index_state == "available":
        assert "2.0-3" in out
        assert "Example available package" in out
        assert "MIT" in out
        assert "sudo pkm install example-available" in out
    else:
        assert "Package 'example-available' is not installed" in out


@pytest.mark.parametrize("helper_without_payload", [False, True])
def test_installed_info_returns_zero_without_consulting_the_index(
        info_db, monkeypatch, capsys, helper_without_payload):
    root, path = info_db
    manager = Mock(side_effect=AssertionError("installed info needs no index"))
    monkeypatch.setattr(cli, "repo_manager", manager)
    monkeypatch.setattr(cli, "helper_is_present", lambda name: helper_without_payload)
    monkeypatch.setattr(cli, "helper_payload_present", lambda name: False)
    with PackageDB(path, root=str(root), read_only=True) as db:
        rc = cli.cmd_info(db, SimpleNamespace(package="example-installed"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "example-installed 1.0-2" in out
    assert "install_date" in out
    assert "Files: 0" in out
    manager.assert_not_called()
    if helper_without_payload:
        assert "payload" in out
        assert "not installed" in out


@pytest.mark.parametrize("command", ["info", "show"])
@pytest.mark.parametrize("installed", [True, False])
def test_cli_process_reports_installed_state_without_writing_the_database(
        info_db, tmp_path, command, installed):
    root, path = info_db
    before = path.read_bytes()
    name = "example-installed" if installed else "example-missing"
    env = os.environ.copy()
    env["IGOS_TRACE_ROOT"] = str(tmp_path / "trace")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pkm", "--root", str(root), "--db", str(path),
         command, name],
        cwd=Path(__file__).resolve().parents[2], env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == (0 if installed else 1), result.stderr
    assert "Traceback" not in result.stderr
    assert ("install_date" if installed else "is not installed") in result.stdout
    assert path.read_bytes() == before
    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()
