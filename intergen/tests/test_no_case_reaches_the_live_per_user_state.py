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
# The account's own per-user state: the four trees the product's writers land
# in when the bases are left at their defaults under the home the password
# database names. These, and not the whole home, are what no case may reach: a
# run whose temporary directory lies inside the home (a machine that keeps test
# runs off /tmp) has every base and every floor inside it, and none in these.
ACCOUNT_TREES = tuple(ACCOUNT_HOME / base / "intergen"
                      for base in (".local/state", ".local/share", ".cache",
                                   ".config"))


def _inside(path, directory) -> bool:
    path = Path(os.path.realpath(path))
    directory = Path(os.path.realpath(directory))
    return path == directory or directory in path.parents


def _reaches_the_accounts_trees(path) -> bool:
    """True when ``path`` lies in one of the account's own per-user trees or
    holds one: a base that holds a tree resolves the product's files into it."""
    return any(_inside(path, tree) or _inside(tree, path)
               for tree in ACCOUNT_TREES)


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
        assert not _reaches_the_accounts_trees(value), (variable, value)


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


# Read when this module is collected, before any case's fixture runs: these
# are the paths the modules fixed at their import, under whatever the
# conftest's import-time layer left in the environment. A module imported
# inside a case would fix its path under the case's own directory and say
# nothing about that layer, which is how a first version of these cases passed
# with the layer removed.
from intergen import config as _config  # noqa: E402
from intergen import mcp_client as _mcp_client  # noqa: E402
from intergen import provider_config as _provider_config  # noqa: E402
from intergen import session_manager as _session_manager  # noqa: E402
from intergen import setup as _setup  # noqa: E402
from intergen import web_server as _web_server  # noqa: E402

FIXED_AT_IMPORT = [
    ("intergen.config._USER_CONFIG", _config._USER_CONFIG),
    ("intergen.provider_config._USER_CONFIG", _provider_config._USER_CONFIG),
    ("intergen.session_manager.SESSIONS_DIR", _session_manager.SESSIONS_DIR),
    ("intergen.web_server.TOKEN_PATH", _web_server.TOKEN_PATH),
    ("intergen.mcp_client._SCHEMA_PIN_DIR", _mcp_client._SCHEMA_PIN_DIR),
    ("intergen.setup.TOKEN_PATH", _setup.TOKEN_PATH),
    ("intergen.setup.SESSIONS_DIR", _setup.SESSIONS_DIR),
]

# The bases as the import-time layer left them, read at the same moment: the
# floor the paths above were fixed under. Which conftest made it depends on the
# run (this directory's, or the project root's when that one already moved
# every base out of the home), so the cases read it rather than name one.
BASES_AT_COLLECTION = {variable: os.environ.get(variable) for variable in BASES}


@pytest.mark.parametrize("name, value", FIXED_AT_IMPORT,
                         ids=[name for name, _ in FIXED_AT_IMPORT])
def test_a_path_fixed_at_import_is_not_the_accounts(name, value):
    assert not _reaches_the_accounts_trees(value), f"{name} = {value}"
    assert any(_inside(value, base) for base in BASES_AT_COLLECTION.values()
               if base), f"{name} = {value}, the floor {BASES_AT_COLLECTION}"


def test_the_floor_at_import_is_not_the_accounts():
    for variable, value in BASES_AT_COLLECTION.items():
        assert value, f"{variable} is not set"
        assert not _reaches_the_accounts_trees(value), (variable, value)


@pytest.mark.parametrize("path", [
    ACCOUNT_HOME,
    ACCOUNT_HOME / ".local" / "state",
    ACCOUNT_HOME / ".local" / "state" / "intergen" / "tool-dispatch.jsonl",
    ACCOUNT_HOME / ".local" / "share" / "intergen" / "sessions",
    ACCOUNT_HOME / ".cache" / "intergen",
    ACCOUNT_HOME / ".config" / "intergen" / "web-token",
], ids=["the home", "the state base", "the audit log", "the sessions",
        "the cache tree", "the token"])
def test_the_account_s_own_trees_are_recognised(path):
    # The positive control: the test the cases above apply detects every way
    # into the account's own state, so their passing says something.
    assert _reaches_the_accounts_trees(path), path


def test_a_floor_inside_the_home_is_not_the_account_s_state():
    assert not _reaches_the_accounts_trees(
        ACCOUNT_HOME / "tmp" / "pytest-of-a" / "per-user0" / "state")
