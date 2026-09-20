# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Installed gate: the owner's account can reach the backup engine.

The engine socket and its directory carry the group that lets a person's own
account connect. On 2026-09-20 two installed machines read root:root on both,
hours after boot, because sibling units re-applied the runtime directory
without the group; the CLI and the backup application were refused while root
clients kept working. This gate reads the real socket on the machine it runs
on, after any timer has fired, so a machine in that state is RED here rather
than green in every tree test.
"""

import grp
import os
import stat

import pytest

SOCKET = "/run/chronicle/engine.sock"
DIRECTORY = "/run/chronicle"
GROUP = "chronicle"


def _gid():
    try:
        return grp.getgrnam(GROUP).gr_gid
    except KeyError:
        pytest.skip(f"no {GROUP!r} group on this machine: the backup engine is not installed")


def _stat(path):
    try:
        return os.stat(path)
    except FileNotFoundError:
        pytest.skip(f"{path} absent: the backup engine is not running here")
    except PermissionError:
        pytest.fail(f"{path} cannot be read by this account: its directory is closed to the "
                    f"{GROUP!r} group (the state measured on 2026-09-20)")


def test_the_engine_directory_belongs_to_the_engine_group():
    st = _stat(DIRECTORY)
    assert st.st_gid == _gid(), (
        f"{DIRECTORY} is group {st.st_gid}, not {GROUP!r}: a unit re-owned the engine's "
        "runtime directory after the engine set it")
    assert stat.S_IMODE(st.st_mode) == 0o750, f"{DIRECTORY} mode {stat.S_IMODE(st.st_mode):04o}, expected 0750"


def test_the_engine_socket_belongs_to_the_engine_group():
    st = _stat(SOCKET)
    assert st.st_gid == _gid(), (
        f"{SOCKET} is group {st.st_gid}, not {GROUP!r}: the owner's account is refused at the "
        "engine while root clients keep working")
    assert stat.S_IMODE(st.st_mode) == 0o660, f"{SOCKET} mode {stat.S_IMODE(st.st_mode):04o}, expected 0660"
