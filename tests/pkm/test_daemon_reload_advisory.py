# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A transaction that replaced a unit definition says which reload is owed.

pkm's canonical systemd hook reloads the SYSTEM manager when a package
replaces a unit file under (usr/lib|etc)/systemd/system. It deliberately
claims nothing under .../systemd/user, and says so where the trigger is
defined: `systemctl daemon-reload` refreshes the system manager only and
reaches no user manager, so matching user units would run a command that
cannot do the job and report a hook that ran.

Naming the gap there was honest. Leaving the person who owns the machine to
discover it was not: measured on an installed machine across a real upgrade
that shipped user units, systemd answered NeedDaemonReload=yes on the
running user managers afterwards and nothing told anyone.

WHAT THE PROPERTY ACTUALLY IS, measured against live systemd 2026-09-22
rather than assumed, because the first shape of this advisory assumed wrong:

  - it belongs to a UNIT, not to the manager. `systemctl show
    --property=NeedDaemonReload` with no unit named returns an EMPTY value
    and exit 0 — an advisory built on that would have reported "could not
    be established" forever;
  - a unit the manager has never heard of answers "no" with exit 0, so a
    "no" does not prove the unit was seen. The advisory therefore only ever
    supports saying a reload IS owed;
  - a unit whose file exists but which the manager has not loaded also
    answers "no" — there is no in-memory state to be stale;
  - a LOADED unit whose file was merely touched answers "yes", with the
    file's contents byte-identical across the touch (sha256 equal). That is
    why the answer is read from systemd and never computed from contents;
  - a template name (`foo@.service`) is refused outright ("neither a valid
    invocation ID nor unit name") and comes back as not established.

What this pins: which paths are unit definitions and which unit each one
belongs to; that the reader asks per unit and accepts only yes or no; that
the line appears only for units systemd says are stale; that an
unestablished answer is stated as unknown rather than as "nothing owed";
and that the pre-transaction ESTIMATE never carries it, because a property
read before the change describes the state before the change.
"""
from __future__ import annotations

import subprocess

import pytest

from pkm import services


# --- which paths are unit definitions, and which unit they belong to ------

@pytest.mark.parametrize("path,expected", [
    ("usr/lib/systemd/system/sshd.service", ("system", "sshd.service")),
    ("etc/systemd/system/sshd.service", ("system", "sshd.service")),
    ("usr/lib/systemd/system/pkm-check-updates.timer",
     ("system", "pkm-check-updates.timer")),
    ("usr/lib/systemd/system/docker.socket", ("system", "docker.socket")),
    ("usr/lib/systemd/system/gdm.service.d/override.conf",
     ("system", "gdm.service")),
    ("usr/lib/systemd/system/graphical.target",
     ("system", "graphical.target")),
    ("usr/lib/systemd/user/pipewire.service", ("user", "pipewire.service")),
    ("etc/systemd/user/gpg-agent.socket", ("user", "gpg-agent.socket")),
    ("usr/lib/systemd/user/app.service.d/tuning.conf",
     ("user", "app.service")),
])
def test_a_unit_definition_is_recognised_and_attributed(path, expected):
    assert services.unit_definitions([path]) == {expected}


@pytest.mark.parametrize("path", [
    "usr/bin/sshd",
    "usr/lib/systemd/system-preset/80-x.preset",
    "usr/lib/systemd/system/",
    "usr/share/doc/foo/example.service",
    "usr/lib/systemd/system/README",
    "etc/init.d/postgresql",
])
def test_a_path_that_is_not_a_unit_definition_is_not_claimed(path):
    assert services.unit_definitions([path]) == set()


def test_both_scopes_are_reported_when_a_package_ships_both():
    assert services.unit_definitions([
        "usr/lib/systemd/system/a.service",
        "usr/lib/systemd/user/b.service",
        "usr/bin/a",
    ]) == {("system", "a.service"), ("user", "b.service")}
    assert services.unit_definition_scopes([
        "usr/lib/systemd/system/a.service",
        "usr/lib/systemd/user/b.service",
    ]) == {"system", "user"}


# --- the property is read, never computed ---------------------------------

class _Run:
    """Stands in for subprocess.run, recording the argv it was given."""

    def __init__(self, stdout="", returncode=0, raises=None):
        self.stdout, self.returncode, self.raises = stdout, returncode, raises
        self.argv = None

    def __call__(self, argv, *a, **kw):
        self.argv = argv
        if self.raises:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.returncode,
                                           stdout=self.stdout, stderr="")


def test_the_system_answer_comes_from_systemd_for_that_unit(monkeypatch):
    run = _Run(stdout="yes\n")
    monkeypatch.setattr(services.subprocess, "run", run)
    assert services.daemon_reload_needed("sshd.service", "system") is True
    assert services.SYSTEMCTL in run.argv
    assert "--user" not in run.argv
    assert "NeedDaemonReload" in run.argv
    assert run.argv[-1] == "sshd.service"


def test_the_user_answer_comes_from_the_user_manager(monkeypatch):
    run = _Run(stdout="yes\n")
    monkeypatch.setattr(services.subprocess, "run", run)
    assert services.daemon_reload_needed("pipewire.service", "user") is True
    assert "--user" in run.argv


def test_no_means_no(monkeypatch):
    monkeypatch.setattr(services.subprocess, "run", _Run(stdout="no\n"))
    assert services.daemon_reload_needed("sshd.service", "system") is False


@pytest.mark.parametrize("kwargs", [
    {"stdout": "", "returncode": 0},          # the manager-object shape
    {"stdout": "", "returncode": 1},
    {"stdout": "Failed to get properties\n", "returncode": 1},
    {"stdout": "maybe\n", "returncode": 0},
    {"raises": FileNotFoundError("systemctl")},
    {"raises": subprocess.TimeoutExpired(cmd="systemctl", timeout=10)},
    {"raises": OSError("no")},
])
def test_anything_that_is_not_a_clear_yes_or_no_is_not_established(
        monkeypatch, kwargs):
    monkeypatch.setattr(services.subprocess, "run", _Run(**kwargs))
    assert services.daemon_reload_needed("x.service", "system") is None


# --- against the live manager on this machine -----------------------------

def test_the_reader_returns_a_real_answer_from_the_running_manager():
    """Not a mock: the shipped command against this machine's own systemd.

    A unit the manager certainly knows answers yes or no; a template name
    is refused and comes back unestablished. Skipped where no manager is
    reachable (a chroot or a container), which is itself the None case.
    """
    answer = services.daemon_reload_needed("systemd-journald.service",
                                           "system")
    if answer is None:
        pytest.skip("no reachable system manager on this host")
    assert answer in (True, False)
    assert services.daemon_reload_needed("a-template@.service",
                                         "system") is None


# --- the line a person reads ----------------------------------------------

def _classification(units, requirement="none", services_=()):
    return {"requirement": requirement, "services": list(services_),
            "reason": "t", "unit_definitions": sorted(units)}


def test_the_reload_is_named_when_systemd_says_that_unit_is_stale(monkeypatch):
    monkeypatch.setattr(services, "daemon_reload_needed",
                        lambda unit, scope: True)
    block = services.format_next_steps(
        [("openssh", _classification({("system", "sshd.service")}))])
    assert "RELOAD SYSTEMD" in block
    assert "sshd.service" in block
    assert "sudo systemctl daemon-reload" in block


def test_the_user_manager_reload_is_named_separately(monkeypatch):
    monkeypatch.setattr(services, "daemon_reload_needed",
                        lambda unit, scope: True)
    block = services.format_next_steps(
        [("pipewire", _classification({("user", "pipewire.service")}))])
    assert "systemctl --user daemon-reload" in block
    assert "sudo systemctl daemon-reload" not in block
    assert "does not reach it" in block


def test_nothing_is_said_when_systemd_says_the_unit_is_current(monkeypatch):
    monkeypatch.setattr(services, "daemon_reload_needed",
                        lambda unit, scope: False)
    block = services.format_next_steps(
        [("openssh", _classification({("system", "sshd.service")}))])
    assert block == ""


def test_an_unestablished_answer_is_stated_as_unknown(monkeypatch):
    monkeypatch.setattr(services, "daemon_reload_needed",
                        lambda unit, scope: None)
    block = services.format_next_steps(
        [("openssh", _classification({("system", "sshd.service")}))])
    assert "state unknown" in block
    assert "sudo systemctl daemon-reload" in block


def test_systemd_is_not_asked_when_no_unit_definition_was_replaced(
        monkeypatch):
    asked = []
    monkeypatch.setattr(services, "daemon_reload_needed",
                        lambda unit, scope: asked.append(unit) or True)
    block = services.format_next_steps([("nano", _classification(set()))])
    assert asked == []
    assert block == ""


def test_the_pre_transaction_estimate_never_carries_it(monkeypatch):
    asked = []
    monkeypatch.setattr(services, "daemon_reload_needed",
                        lambda unit, scope: asked.append(unit) or True)
    block = services.format_next_steps(
        [("openssh", _classification({("system", "sshd.service")}))],
        estimate=True)
    assert asked == []
    assert "daemon-reload" not in block


def test_the_classifier_records_the_units_it_saw():
    c = services.classify_restart_requirement(
        "pipewire", ["usr/lib/systemd/user/pipewire.service", "usr/bin/x"])
    assert c["unit_definitions"] == [["user", "pipewire.service"]] or \
        c["unit_definitions"] == [("user", "pipewire.service")]
    c = services.classify_restart_requirement("nano", ["usr/bin/nano"])
    assert c["unit_definitions"] == []


def test_a_reload_line_coexists_with_a_restart_line(monkeypatch):
    """The reload is its own owed action; it does not replace or hide the
    others."""
    monkeypatch.setattr(services, "daemon_reload_needed",
                        lambda unit, scope: True)
    block = services.format_next_steps([(
        "openssh",
        _classification({("system", "sshd.service")},
                        requirement="restart", services_=["sshd.service"]),
    )])
    assert "RESTART SERVICES" in block
    assert "RELOAD SYSTEMD" in block


def test_each_replaced_unit_is_asked_about_once(monkeypatch):
    asked = []
    monkeypatch.setattr(services, "daemon_reload_needed",
                        lambda unit, scope: asked.append((scope, unit)) or True)
    services.format_next_steps([
        ("a", _classification({("system", "one.service"),
                               ("system", "two.timer")})),
        ("b", _classification({("system", "one.service"),
                               ("user", "three.service")})),
    ])
    assert sorted(asked) == [("system", "one.service"),
                             ("system", "two.timer"),
                             ("user", "three.service")]
