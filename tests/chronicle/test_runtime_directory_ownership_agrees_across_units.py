# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Every unit that declares the engine's runtime directory states the engine's group.

WHY THIS FILE EXISTS. On 2026-09-20 two installed machines refused the owner's
own account at the Chronicle engine socket: /run/chronicle/engine.sock was
root:root 0660 and /run/chronicle root:root 0755, while the engine had created
them root:chronicle 0660 / 0750 and verified that at startup. systemd re-applies
a RuntimeDirectory's owner, group and mode — recursively — every time any unit
that declares it starts, and three timer-driven siblings declared it with no
Group= and the default mode. The first timer after boot handed the directory and
the socket back to root; the client then told the person to join a group they
were already in. Root clients kept working, so captures continued and the owner
could neither see nor restore them.

WHAT THESE TESTS PIN.
1. Every shipped unit that declares RuntimeDirectory=chronicle also states
   Group=chronicle and RuntimeDirectoryMode=0750 — the engine's own values —
   so whichever unit starts last leaves the directory as the engine needs it.
2. The refusal message says what it measured: for an account inside the group
   it names the socket's measured owner and mode and a restart, never usermod;
   for an account outside it, the usermod remedy as before.

Nothing here starts a unit; systemd's re-application itself was measured on a
running machine with transient units on a scratch directory (evidence held by
the project), and the installed gate tests/installed/test_gate_chronicle_socket_group.py
reads the real socket on an installed machine.
"""

import re
from pathlib import Path

import chronicle.api as _api

REPO = Path(__file__).resolve().parents[2]
UNITS = REPO / "assets" / "intergenos-backup" / "systemd"
ENGINE_GROUP = "chronicle"
ENGINE_DIR_MODE = "0750"


def _directives(unit: Path) -> dict:
    """Last value wins, as systemd reads a unit file. Comments and blanks skipped."""
    out = {}
    for raw in unit.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";", "[")):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def units_declaring_the_engine_directory():
    found = []
    for unit in sorted(UNITS.glob("*.service")):
        d = _directives(unit)
        if d.get("RuntimeDirectory") == ENGINE_GROUP:
            found.append((unit.name, d))
    return found


def test_the_engine_directory_is_declared_by_more_than_the_engine():
    names = [n for n, _ in units_declaring_the_engine_directory()]
    assert "chronicled.service" in names
    assert len(names) >= 2, names  # the siblings the defect came from are still here


def test_every_unit_declaring_the_directory_states_the_engine_group_and_mode():
    wrong = []
    for name, d in units_declaring_the_engine_directory():
        if d.get("Group") != ENGINE_GROUP or d.get("RuntimeDirectoryMode") != ENGINE_DIR_MODE:
            wrong.append((name, d.get("Group"), d.get("RuntimeDirectoryMode")))
    assert not wrong, (
        f"units declaring RuntimeDirectory={ENGINE_GROUP} without Group={ENGINE_GROUP} and "
        f"RuntimeDirectoryMode={ENGINE_DIR_MODE}: {wrong}. systemd re-applies owner, group and "
        "mode on every start of every declaring unit, so the first of these to start after "
        "boot re-owns the engine's socket to root and the owner's account is refused."
    )


def test_the_engine_group_in_the_units_is_the_group_the_code_names():
    assert _api.ENGINE_SOCKET_GROUP == ENGINE_GROUP
    assert f"{_api.ENGINE_RUNTIME_DIR_MODE:04o}" == ENGINE_DIR_MODE


# --- the refusal message reports what it measured ---------------------------

def test_an_account_inside_the_group_is_told_the_socket_is_the_problem(tmp_path):
    sock = tmp_path / "engine.sock"
    sock.write_bytes(b"")
    msg = _api.access_denied_message(str(sock), in_group=True)
    assert "IS in" in msg and "measured" in msg
    assert str(sock) in msg
    assert re.search(r"\b\S+:\S+ [0-7]{4}\b", msg), msg  # an owner:group mode reading
    assert "systemctl restart chronicled.service" in msg
    assert "usermod" not in msg


def test_an_account_outside_the_group_keeps_the_membership_remedy():
    msg = _api.access_denied_message("/nonexistent/engine.sock", in_group=False)
    assert msg == _api.ACCESS_DENIED_MESSAGE
    assert "usermod" in msg


def test_an_unreadable_socket_is_reported_as_unreadable_not_guessed(tmp_path):
    msg = _api.access_denied_message(str(tmp_path / "closed" / "engine.sock"), in_group=True)
    assert "not readable" in msg
