# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Bind-ownership readiness — a foreign holder on the port must never count as ours.

Regression guard for the GDM-greeter cold-boot embedding port collision: the
greeter session's own InterGen daemon binds 8080/8081 first, our child cannot
bind and exits, and the readiness check false-positived on the greeter server's
/health answer (mask-not-verify). These tests prove the bind-ownership
primitives distinguish "our child owns the listening socket" from "a stranger
holds the port" — the basis for the pre-launch refusal (StartFailure.PORT_IN_USE)
and the post-health ownership gate in LlamaManager.start()/_wait_for_healthy.
"""
from __future__ import annotations

import contextlib
import os
import socket

from intergen.llama_manager import LlamaManager
from intergen.interfaces.types import StartFailure


@contextlib.contextmanager
def _listening_socket():
    """Bind+listen a real socket on an ephemeral 127.0.0.1 port; yield the port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    try:
        yield s.getsockname()[1]
    finally:
        s.close()


def _likely_free_port() -> int:
    """An almost-certainly-free port number (bind ephemeral, release it)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_port_has_listener_detects_occupied_port():
    mgr = LlamaManager()
    with _listening_socket() as port:
        assert mgr._port_has_listener(port) is True


def test_port_has_listener_false_for_free_port():
    mgr = LlamaManager()
    assert mgr._port_has_listener(_likely_free_port()) is False


def test_pid_owns_port_true_for_owning_process():
    """The process actually holding the listening socket is recognized as owner."""
    mgr = LlamaManager()
    with _listening_socket() as port:
        assert mgr._pid_owns_port(os.getpid(), port) is True


def test_pid_owns_port_false_for_foreign_holder():
    """A different process is NOT credited with our socket — the greeter case:
    a stranger answering on the port can never satisfy the readiness gate."""
    mgr = LlamaManager()
    with _listening_socket() as port:
        # The parent process is readable (same user) but does not hold this
        # socket — so ownership must be False, not a false-positive.
        assert mgr._pid_owns_port(os.getppid(), port) is False


def test_pid_owns_port_false_when_nothing_listens():
    mgr = LlamaManager()
    assert mgr._pid_owns_port(os.getpid(), _likely_free_port()) is False


def test_port_in_use_is_operational_not_integrity():
    """PORT_IN_USE is the benign "didn't come up" class (greeter held the port),
    not a declared-capability integrity failure — so it degrades + retries via
    the watchdog rather than latching the conspicuous integrity-failure state."""
    assert StartFailure.PORT_IN_USE.is_integrity is False


class _FakeProc:
    """Minimal live-process stand-in: poll() is None (alive), pid set."""

    def __init__(self, pid: int):
        self.pid = pid

    def poll(self):
        return None


class _FakeConfig:
    def __init__(self, port: int):
        self.port = port


def test_owns_port_false_when_not_running():
    """No child process → owns_port is False (it can own nothing). Guards the
    periodic probe against crediting ownership to a dead/never-started server."""
    mgr = LlamaManager()
    assert mgr.owns_port() is False


def test_owns_port_true_when_our_child_holds_the_port():
    """A running child that holds the LISTEN socket IS recognized as owner — the
    ownership-aware periodic probe's healthy case (steady state, no churn)."""
    mgr = LlamaManager()
    with _listening_socket() as port:
        mgr._process = _FakeProc(os.getpid())
        mgr._config = _FakeConfig(port)
        assert mgr.owns_port() is True


def test_owns_port_false_for_foreign_holder():
    """A running child whose port is actually held by a DIFFERENT process (the
    greeter's daemon answering /health green) is NOT owner — the periodic probe
    rejects the foreign holder so the watchdog rebinds rather than reading the
    cold-boot collision as healthy. This is punch-list #1: the periodic probe is
    now ownership-aware, matching the startup bind-ownership gate."""
    mgr = LlamaManager()
    with _listening_socket() as port:
        # Our 'child' pid (the parent process) does not hold this socket — the
        # test process does — so ownership must be False, not a false-positive.
        mgr._process = _FakeProc(os.getppid())
        mgr._config = _FakeConfig(port)
        assert mgr.owns_port() is False
