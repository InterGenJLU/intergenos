"""Exercise handler isolation at the real mutating command boundaries."""

import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pkm import cli, pretxn, rootpaths


class _HandlersConsulted(Exception):
    """Stop before a command can enter its package mutation loop."""


def _drive_command(monkeypatch, verb, root, expected_directory):
    """Run the command and real enumeration; intercept any wrong path first."""
    installed = {"name": "example", "version": "1.0", "release": 1}
    remote = {"name": "example", "version": "2.0", "release": 1,
              "depends": [], "size": 0}
    db = SimpleNamespace(
        list_held=lambda: [], list_installed=lambda: [installed])

    def forbidden_mutation(*args, **kwargs):
        pytest.fail("command reached package mutation")

    installer = SimpleNamespace(install=forbidden_mutation)
    remover = SimpleNamespace(remove=forbidden_mutation)
    repo = SimpleNamespace(get_package=lambda name: remote)
    monkeypatch.setattr(cli, "_INSTALL_ROOT", root)
    monkeypatch.setattr(cli, "package_installer", lambda db: installer)
    monkeypatch.setattr(cli, "PackageRemover", lambda db: remover)
    monkeypatch.setattr(cli, "repo_manager", lambda: repo)
    monkeypatch.setattr(cli, "_print_upgrade_plan_summary", lambda *args: None)
    monkeypatch.setattr(cli, "_confirm_upgrade", lambda args: True)

    args = cli.build_parser().parse_args([verb, "example"])
    consulted = []
    actual_is_dir = Path.is_dir
    actual_list_handlers = pretxn.list_handlers

    def guarded_is_dir(path):
        # Check BEFORE stat or enumeration, even when the regression returns.
        assert path == expected_directory, (
            f"handler lookup escaped isolated directory: {path}")
        consulted.append(path)
        return actual_is_dir(path)

    def inspect_then_stop(handler_dir=None):
        assert actual_list_handlers(handler_dir) == []
        raise _HandlersConsulted

    with patch.object(Path, "is_dir", guarded_is_dir), \
            patch.object(pretxn, "list_handlers", inspect_then_stop):
        with pytest.raises(_HandlersConsulted):
            getattr(cli, f"cmd_{verb}")(db, args)
    assert consulted == [expected_directory]


@pytest.mark.parametrize("verb", ["install", "remove", "upgrade"])
@pytest.mark.parametrize("alternate_root", [False, True])
def test_commands_use_isolated_handler_directory(
        monkeypatch, tmp_path, verb, alternate_root):
    isolated = Path(os.environ["PKM_PRETXN_HANDLER_DIR"])
    root = tmp_path / "target" if alternate_root else None
    _drive_command(monkeypatch, verb, root, isolated)


@pytest.mark.parametrize("verb", ["install", "remove", "upgrade"])
def test_commands_preserve_target_handlers_without_override(
        monkeypatch, tmp_path, verb):
    monkeypatch.delenv("PKM_PRETXN_HANDLER_DIR")
    root = tmp_path / "target"
    handlers = rootpaths.pretxn_handler_dir(root)
    handlers.mkdir(parents=True)
    _drive_command(monkeypatch, verb, root, handlers)


def test_handler_override_is_read_at_call_time(monkeypatch, tmp_path):
    monkeypatch.setenv("PKM_PRETXN_HANDLER_DIR", str(tmp_path))
    executable = tmp_path / "handler"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    assert pretxn.list_handlers() == [executable]


def test_explicit_handler_directory_takes_precedence(tmp_path):
    executable = tmp_path / "handler"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    assert pretxn.list_handlers(tmp_path) == [executable]


def test_subprocess_and_reimport_inherit_isolation():
    code = """
import importlib
import os
from pathlib import Path
from unittest.mock import patch
from pkm import pretxn

expected = Path(os.environ["PKM_PRETXN_HANDLER_DIR"])
visited = []
actual_is_dir = Path.is_dir

def guarded_is_dir(path):
    assert path == expected, f"handler lookup escaped isolated directory: {path}"
    visited.append(path)
    return actual_is_dir(path)

with patch.object(Path, "is_dir", guarded_is_dir):
    assert pretxn.list_handlers() == []
    importlib.reload(pretxn)
    assert pretxn.list_handlers() == []
assert visited == [expected, expected]
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[2], timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("override", ["", "relative-handlers"])
def test_invalid_override_refuses_before_lookup(monkeypatch, override):
    monkeypatch.setenv("PKM_PRETXN_HANDLER_DIR", override)
    with patch.object(Path, "is_dir", side_effect=AssertionError("lookup ran")):
        with pytest.raises(ValueError, match="absolute"):
            pretxn.list_handlers()
