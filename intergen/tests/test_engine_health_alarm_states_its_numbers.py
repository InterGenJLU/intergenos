# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The sustained-corruption alarm states the count that fired it.

Measured on an outside user's installed machine: the journal carried
"ENGINE-HEALTH: sustained semantic-corruption flags (0 in the recent
five-generation window)" — an alarm whose own number says nothing is wrong.

The cause is in the monitor: record() CLEARS its rolling window when it
triggers, and the daemon's handler runs on a background thread afterwards and
asks the monitor how many flags are in the window. By then the answer is zero.
A reader cannot tell that from a self-contradicting line, and an alarm nobody
can read is worse than no alarm.

So the monitor keeps the snapshot that fired it, and the handler reports that
count with the threshold and window it was measured against — and does not raise
the alarm at all when the count it has is below the threshold.
"""
from __future__ import annotations

import unittest
from unittest import mock

from intergen.dbus_daemon import InterGenDaemon
from intergen.engine_health import EngineHealthMonitor


def _sync_monitor():
    calls: list[int] = []
    m = EngineHealthMonitor(lambda: calls.append(1), scheduler=lambda fn: fn())
    return m, calls


class MonitorSnapshotTests(unittest.TestCase):
    def test_the_trigger_snapshot_survives_the_window_clear(self):
        m, _calls = _sync_monitor()
        for f in [["x"], ["x"], ["x"]]:
            m.record(f)
        self.assertEqual(m.flagged_in_window(), 0,
                         "the window is cleared on trigger — that part is by design")
        snap = m.last_trigger()
        self.assertIsNotNone(snap, "the count that fired the alarm was not kept")
        self.assertEqual(snap.flagged, 3)
        self.assertEqual(snap.threshold, 3)
        self.assertEqual(snap.window, 5)

    def test_no_snapshot_before_anything_fires(self):
        m, _calls = _sync_monitor()
        for f in [["x"], [], []]:
            m.record(f)
        self.assertIsNone(m.last_trigger())


class _Glass:
    def __init__(self):
        self.events = []

    def emit(self, *a, **k):
        self.events.append((a, k))


def _daemon(monitor):
    d = InterGenDaemon.__new__(InterGenDaemon)
    d._engine_health = monitor
    d._engine_health_flagged = None
    return d


class AlarmMessageTests(unittest.TestCase):
    def test_the_alarm_states_count_threshold_and_window(self):
        m, _calls = _sync_monitor()
        for f in [["x"], ["x"], ["x"]]:
            m.record(f)
        d = _daemon(m)
        with mock.patch("intergen.dbus_daemon.glass", _Glass()), \
             self.assertLogs("intergen.dbus_daemon", level="ERROR") as logs:
            d._on_engine_health_degraded()
        line = "\n".join(logs.output)
        self.assertIn("ENGINE-HEALTH", line)
        self.assertIn("3", line)
        self.assertIn("threshold", line.lower())
        self.assertNotIn("(0 in", line, "the alarm still reports an empty window")
        self.assertIn("3", d._engine_health_flagged)

    def test_the_glass_event_carries_the_same_numbers(self):
        m, _calls = _sync_monitor()
        for f in [["x"], ["x"], ["x"]]:
            m.record(f)
        d = _daemon(m)
        g = _Glass()
        with mock.patch("intergen.dbus_daemon.glass", g), \
             self.assertLogs("intergen.dbus_daemon", level="ERROR"):
            d._on_engine_health_degraded()
        self.assertTrue(g.events, "no glass event was emitted")
        detail = g.events[0][1]["detail"]
        self.assertEqual(detail["flagged"], 3)
        self.assertEqual(detail["threshold"], 3)
        self.assertEqual(detail["window"], 5)

    def test_no_alarm_when_the_count_is_below_the_threshold(self):
        # A handler called without a trigger snapshot (a caller that reached it
        # some other way) must not print an alarm whose own number denies it.
        m, _calls = _sync_monitor()
        m.record(["x"])
        d = _daemon(m)
        with mock.patch("intergen.dbus_daemon.glass", _Glass()), \
             self.assertLogs("intergen.dbus_daemon", level="WARNING") as logs:
            d._on_engine_health_degraded()
        line = "\n".join(logs.output)
        self.assertNotIn("ENGINE-HEALTH:", line)
        self.assertIsNone(d._engine_health_flagged)


if __name__ == "__main__":
    unittest.main()
