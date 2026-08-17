# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Boot-path bind-race recovery — chat-engine bring-up waits for the port to
actually accept a bind before launching.

Regression guard for the boot-time race that left the chat server silently
unserved: _reap_stale_owner freed the LISTEN socket, start() relaunched
immediately, and the child lost the bind() to a socket still holding the port
(one the LISTEN-only checks cannot see) and exited — with no retry (the
embedding server recovered via the watchdog; the chat engine had no
equivalent). _await_port_bindable probes the real bind with bounded backoff and
fails LOUD on exhaustion rather than launching into a guaranteed lost bind.

The probe's whole value is that it PREDICTS the child's bind, so it must use the
child's socket options. Measured on a real restart boundary: a stopped server
leaves its served connections in TIME_WAIT on the serving port, llama-server
binds straight over them in ~0.1s because it sets SO_REUSEADDR, and a probe
without that option refuses for the kernel's full 60s TIME_WAIT timeout. A probe
stricter than the child does not protect anything — it blocks starts that would
have succeeded. SO_REUSEADDR does not weaken the gate: it never permits a bind
while another socket is actively LISTENING, which the last test here pins.
"""
from __future__ import annotations

import contextlib
import socket

from intergen import llama_manager
from intergen.llama_manager import LlamaManager


@contextlib.contextmanager
def _listening_socket():
    """Bind+listen on an ephemeral 127.0.0.1 port; yield (socket, port)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    try:
        yield s, s.getsockname()[1]
    finally:
        with contextlib.suppress(OSError):
            s.close()


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_await_bindable_true_immediately_for_free_port(monkeypatch):
    """A free port binds on the first probe — no wait, no false negative."""
    slept: list[float] = []
    monkeypatch.setattr(llama_manager.time, "sleep", lambda d: slept.append(d))
    mgr = LlamaManager()
    assert mgr._await_port_bindable(_free_port()) is True
    assert slept == []          # first attempt succeeded; never backed off


def test_await_bindable_false_when_port_stays_held(monkeypatch):
    """A port held for the whole budget → False, so start() fails LOUD instead
    of launching into a guaranteed lost bind. Sleep is stubbed so the bounded
    backoff does not actually wall-clock the ~11.5s budget in the test."""
    monkeypatch.setattr(llama_manager.time, "sleep", lambda d: None)
    mgr = LlamaManager()
    with _listening_socket() as (_sock, port):
        assert mgr._await_port_bindable(port) is False


def test_await_bindable_recovers_when_port_frees_during_backoff(monkeypatch):
    """The race's happy path: the port is held on the first probe, then the
    lingering socket releases while we back off — the retry binds and returns
    True. Proves the wait RECOVERS a transient hold rather than refusing it."""
    with _listening_socket() as (sock, port):
        # Release the port the first time we back off, standing in for the
        # lingering socket draining during the wait; the next probe then binds.
        def _release_then_noop(_delay, _sock=sock):
            with contextlib.suppress(OSError):
                _sock.close()
        monkeypatch.setattr(llama_manager.time, "sleep", _release_then_noop)
        mgr = LlamaManager()
        assert mgr._await_port_bindable(port) is True


@contextlib.contextmanager
def _server_side_time_wait():
    """Leave a real server-side TIME_WAIT on a 127.0.0.1 port; yield the port.

    Built the way the serving port actually gets one: a listener accepts a
    connection, the SERVER closes it first (which is what puts the local end in
    TIME_WAIT), then the listener itself closes. This is the exact state a
    just-stopped model server leaves behind on its serving port.
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port))
    conn, _ = srv.accept()
    conn.close()                      # server closes first -> local TIME_WAIT
    srv.close()
    try:
        yield port
    finally:
        with contextlib.suppress(OSError):
            client.close()


def _has_time_wait(port: int) -> bool:
    """True while the kernel still holds a TIME_WAIT for this local port."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", port))   # no SO_REUSEADDR: refused by TIME_WAIT
        return False
    except OSError:
        return True
    finally:
        probe.close()


def test_await_bindable_true_over_a_server_side_time_wait():
    """The defect this probe had: a just-stopped server's TIME_WAIT made the
    probe refuse a port the child binds over immediately.

    llama-server sets SO_REUSEADDR, so it binds straight through this state; a
    probe that does not is stricter than the thing it claims to predict, and it
    spent its whole retry budget refusing starts that would have succeeded. The
    probe must return True here, on the FIRST attempt, with no backoff at all.
    """
    with _server_side_time_wait() as port:
        if not _has_time_wait(port):
            import pytest
            pytest.skip("kernel released the TIME_WAIT before the assertion")
        mgr = LlamaManager()
        assert mgr._await_port_bindable(port) is True


def test_await_bindable_still_refuses_a_live_listener():
    """The anti-mask pin: SO_REUSEADDR must not buy a bind past a real owner.

    It lifts only the lingering-socket case; binding while another socket is
    actively LISTENING would require SO_REUSEPORT, which is deliberately not
    set. If this ever passes, the probe has stopped being a gate.
    """
    import unittest.mock as _mock
    with _listening_socket() as (_sock, port):
        with _mock.patch.object(llama_manager.time, "sleep", lambda d: None):
            mgr = LlamaManager()
            assert mgr._await_port_bindable(port) is False


# --- the wait has to outlast a killed GPU process's kernel teardown ----------
#
# Measured 2026-08-13 on the box that produced the incident: the watchdog
# SIGKILLed a wedged llama-server at 18:39:16, and the port was STILL refusing a
# bind on the probe logged at 18:40:54 — 98 seconds later. It had released by
# the manual restart at 18:44:56. So the real teardown on this hardware lasted
# somewhere between 98 and 338 seconds, while the wait's whole budget was
# 0.5+1+2+4+4 = 11.5 seconds.
#
# The consequence was not a slow recovery, it was NO recovery: every attempt
# burned its budget in 11.5s, logged "refusing to launch into a lost bind", and
# the machine sat without a language model until a person restarted the service.
# Refusal is the right verdict for a port that is genuinely lost. It is not a
# verdict a wait can honestly reach in a twentieth of the time the release takes.
#
# 98 seconds is a MEASURED LOWER BOUND, so it is what the guard asserts against.
OBSERVED_TEARDOWN_FLOOR_S = 98.0


def test_the_bind_wait_outlasts_the_observed_gpu_teardown(monkeypatch):
    """A port that frees only after the teardown this box actually produced must
    be RECOVERED, not refused.

    The port is really held, and the backoff is really driven — only the
    sleeping is virtual, so the test does not wall-clock two minutes. The
    lingering socket is released once the code has waited as long as the real
    teardown lasted.
    """
    with _listening_socket() as (sock, port):
        waited = {"total": 0.0}

        def _virtual_sleep(delay, _sock=sock):
            waited["total"] += delay
            if waited["total"] >= OBSERVED_TEARDOWN_FLOOR_S:
                with contextlib.suppress(OSError):
                    _sock.close()
        monkeypatch.setattr(llama_manager.time, "sleep", _virtual_sleep)
        mgr = LlamaManager()
        assert mgr._await_port_bindable(port) is True, (
            f"the wait gave up after {waited['total']:.1f}s of backoff; the "
            f"teardown measured on this hardware ran to at least "
            f"{OBSERVED_TEARDOWN_FLOOR_S}s")


def test_the_bind_wait_is_still_bounded(monkeypatch):
    """The anti-mask pin for the longer budget: a port held forever must still
    lose, and in bounded time. A wait that never gives up is not patience, it is
    a hang — the watchdog thread would never reach another health check."""
    waited = {"total": 0.0}

    def _virtual_sleep(delay):
        waited["total"] += delay
    monkeypatch.setattr(llama_manager.time, "sleep", _virtual_sleep)
    with _listening_socket() as (_sock, port):
        mgr = LlamaManager()
        assert mgr._await_port_bindable(port) is False
    assert waited["total"] < 1800.0, (
        f"the wait would spend {waited['total']:.0f}s before refusing")
