# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""No case in this directory reaches the account's own per-user state.

The product's writers resolve their files from the XDG bases and HOME: the
dispatch audit log, the glass log, the conversation sessions, the token. The
project-root conftest moves every base for the whole run, but a run that does
not load it kept the account's real ones, and one module run that way wrote
twenty audit rows into the account's live tool-dispatch.jsonl (2026-10-21).
This directory's conftest now holds the floor itself: at its import, before any
product module, when the bases still point into the account's home, and for
every case, at a directory of the case's own. These cases state both layers
against the paths the product itself resolves, so a run that loses either one
fails here instead of writing somewhere it should not.
"""

from __future__ import annotations

import os
import pwd
from pathlib import Path

import pytest

BASES = ("XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME",
         "HOME")
ACCOUNT_HOME = Path(os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir))


def _inside(path, directory) -> bool:
    path = Path(os.path.realpath(path))
    directory = Path(os.path.realpath(directory))
    return path == directory or directory in path.parents


def _the_cases_own(tmp_path_factory) -> Path:
    """The per-user directory the conftest made for the running case: the one
    every base points into, below this run's base temporary directory."""
    home = Path(os.environ["HOME"])
    assert home.name == "home", home
    root = home.parent
    assert root.name.startswith("per-user"), root
    assert _inside(root, tmp_path_factory.getbasetemp()), root
    return root


def test_every_base_is_a_directory_of_the_cases_own(tmp_path_factory):
    root = _the_cases_own(tmp_path_factory)
    for variable in BASES:
        value = os.environ.get(variable)
        assert value, f"{variable} is not set"
        assert Path(value).parent == root, (variable, value)
        assert Path(value).is_dir(), (variable, value)
        assert not _inside(value, ACCOUNT_HOME), (variable, value)


def test_the_writers_resolve_into_the_cases_directory(tmp_path_factory):
    from intergen import audit_log, glass, private_state
    root = _the_cases_own(tmp_path_factory)
    assert _inside(audit_log.default_log_path(), root / "state")
    assert _inside(glass.default_glass_path(), root / "state")
    assert _inside(private_state.state_dir_path(), root / "state")
    assert _inside(private_state.data_dir_path(), root / "data")
    assert _inside(private_state.config_dir_path(), root / "config")
    assert _inside(private_state.cache_dir_path(), root / "cache")
    assert Path.home() == root / "home"


def test_a_record_written_by_the_audit_writer_lands_in_the_case(tmp_path_factory):
    from intergen import audit_log
    from intergen.interfaces.provenance import AuditRecord
    root = _the_cases_own(tmp_path_factory)
    record = AuditRecord(
        timestamp="2026-10-21T00:00:00+00:00", tool_name="read_file",
        arguments={"path": "/x"}, declared_provenance="user_direct",
        effective_provenance="user_direct", ingress_tools_this_turn=[],
        user_decision="executed", exit_code=0, result_summary="case row")
    assert audit_log.write_record(record)
    written = sorted(p for p in root.rglob("*") if p.is_file())
    assert written == [root / "state" / "intergen" / "tool-dispatch.jsonl"]


@pytest.mark.parametrize("module, name", [
    ("intergen.config", "_USER_CONFIG"),
    ("intergen.provider_config", "_USER_CONFIG"),
    ("intergen.session_manager", "SESSIONS_DIR"),
    ("intergen.web_server", "TOKEN_PATH"),
    ("intergen.mcp_client", "_SCHEMA_PIN_DIR"),
    ("intergen.setup", "TOKEN_PATH"),
    ("intergen.setup", "SESSIONS_DIR"),
])
def test_a_path_fixed_at_import_is_not_the_accounts(module, name):
    import importlib
    value = getattr(importlib.import_module(module), name)
    assert not _inside(value, ACCOUNT_HOME), f"{module}.{name} = {value}"
