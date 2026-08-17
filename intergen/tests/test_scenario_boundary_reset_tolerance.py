# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A long turn must not abort the run at the next scenario boundary.

The measured failure: the corpus's tier-limit asks legitimately provoke long
generations. One ran 137.6s while the client's Ask bound is 120s, so the client
stopped waiting and returned an error-shaped result — but the DAEMON kept
generating. The next scenario's start-of-scenario ResetConversation then hit a
daemon that could not answer a bus call yet, gave up after its own single short
timeout, and the contamination guard aborted the run. Nothing was actually
wrong: the daemon was healthy throughout, its restart counter never moved, and
the reset landed the moment the generation finished.

The mechanism is the same shape as the serving-port probe defect: the instrument
applied a stricter wait than the system it measures. The reset's per-attempt
timeout was sized independently of the Ask bound that governs when the runner
walks away from a turn, so it could give up while a legitimately-running turn
still had time left on the clock.

These tests pin both directions. A busy daemon must be tolerated up to a budget
derived from the Ask bound; a genuinely broken one must still abort, fast, with
no waiting — the contamination guard's abort-rather-than-grade-dirty contract is
patience extended, never permissiveness.
"""

from __future__ import annotations

import unittest
from unittest import mock

from gi.repository import Gio, GLib

from intergen.tests import client as client_mod
from intergen.tests.client import InterGenTestClient


def _timeout_error() -> GLib.Error:
    """The exact error a bus call raises when the peer did not answer in time."""
    return GLib.Error.new_literal(
        Gio.io_error_quark(), "Timeout was reached",
        int(Gio.IOErrorEnum.TIMED_OUT))


def _name_gone_error() -> GLib.Error:
    """A genuinely-absent daemon: the name has no owner. Not busy — broken."""
    return GLib.Error.new_literal(
        Gio.io_error_quark(), "The name is not activatable",
        int(Gio.IOErrorEnum.NOT_FOUND))


class _FakeBus:
    """A bus whose ResetConversation is busy for the first ``busy_calls`` calls.

    Stands in for a daemon still finishing a turn the run walked away from: the
    call does not fail, it simply cannot be answered yet.
    """

    def __init__(self, busy_calls: int, then: str = '{"reset": true}',
                 error_factory=_timeout_error) -> None:
        self.busy_calls = busy_calls
        self.calls = 0
        self._then = then
        self._error_factory = error_factory

    def call_sync(self, *_a, **_kw):
        self.calls += 1
        if self.calls <= self.busy_calls:
            raise self._error_factory()
        return GLib.Variant("(s)", (self._then,))


def _client_with(bus: _FakeBus) -> InterGenTestClient:
    c = InterGenTestClient.__new__(InterGenTestClient)   # no bus, no daemon
    c._mode = "dbus"
    c._daemon = None
    c._bus = bus
    return c


class ResetToleratesABusyDaemonTests(unittest.TestCase):

    def setUp(self):
        # The retry delay and the budget are real wall-clock. Both are replaced
        # by a fake clock that advances only when the code sleeps, so the budget
        # ARITHMETIC is exercised exactly while the test stays instant.
        self.now = 0.0

        def _sleep(seconds: float) -> None:
            self.now += seconds

        patchers = [
            mock.patch.object(client_mod.time, "sleep", _sleep),
            mock.patch.object(client_mod.time, "monotonic", lambda: self.now),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_reset_waits_out_a_daemon_still_finishing_an_abandoned_turn(self):
        """The defect: this aborted a run whose daemon was perfectly healthy."""
        bus = _FakeBus(busy_calls=5)
        _client_with(bus)._reset_conversation_dbus()
        self.assertEqual(bus.calls, 6, "should have retried until it landed")

    def test_reset_budget_is_derived_from_the_ask_bound(self):
        """The sizing is the fix. A reset that gives up before the abandoned
        turn can possibly finish is guaranteed to abort a legitimate run."""
        self.assertGreaterEqual(
            client_mod.RESET_BUSY_BUDGET_S,
            client_mod.ASK_CALL_TIMEOUT_MS / 1000.0,
            "the reset must not give up before an abandoned turn can finish")

    def test_reset_still_fails_loud_once_the_budget_is_spent(self):
        """Patience, not permissiveness: a daemon that never answers still
        aborts rather than let a contaminated conversation be graded."""
        bus = _FakeBus(busy_calls=10_000)
        with self.assertRaises(RuntimeError) as ctx:
            _client_with(bus)._reset_conversation_dbus()
        self.assertIn("still busy", str(ctx.exception))
        self.assertIn("contaminated", str(ctx.exception))
        self.assertGreaterEqual(self.now, client_mod.RESET_BUSY_BUDGET_S,
                                "must not give up before the budget is spent")

    def test_a_broken_daemon_aborts_immediately_with_no_waiting(self):
        """A missing daemon is not a busy one. Burning the busy budget on it
        would turn a fast honest abort into a two-minute stall."""
        bus = _FakeBus(busy_calls=10_000, error_factory=_name_gone_error)
        with self.assertRaises(RuntimeError) as ctx:
            _client_with(bus)._reset_conversation_dbus()
        self.assertEqual(bus.calls, 1, "must not retry a broken daemon")
        self.assertIn("bus-level failure", str(ctx.exception))

    def test_a_refused_reset_is_still_fatal_after_a_busy_wait(self):
        """Waiting out a busy daemon must not soften the result check: a daemon
        that answers but reports it did NOT reset is still a hard abort."""
        bus = _FakeBus(busy_calls=2, then='{"reset": false}')
        with self.assertRaises(Exception) as ctx:
            _client_with(bus)._reset_conversation_dbus()
        self.assertNotIsInstance(ctx.exception, AssertionError)


if __name__ == "__main__":
    unittest.main()
