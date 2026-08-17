# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Engine-health reaction ladder (intergen.engine_health).

The escalation handler itself lives in the daemon (it needs the engine); here we
pin the MONITOR's counting and trigger contract, with a synchronous scheduler so
no threads are spawned.
"""
from __future__ import annotations

import unittest

from intergen.engine_health import EngineHealthMonitor


def _sync_monitor():
    calls: list[int] = []
    m = EngineHealthMonitor(lambda: calls.append(1), scheduler=lambda fn: fn())
    return m, calls


class MonitorTests(unittest.TestCase):
    def test_three_in_five_triggers_once(self) -> None:
        m, calls = _sync_monitor()
        seq = [[], ["x"], [], ["x"], ["x"]]  # 3 flagged within the 5-window
        triggers = [m.record(f) for f in seq]
        self.assertEqual(triggers, [False, False, False, False, True])
        self.assertEqual(len(calls), 1)

    def test_two_in_five_does_not_trigger(self) -> None:
        m, calls = _sync_monitor()
        for f in [["x"], [], [], [], ["x"]]:
            m.record(f)
        self.assertEqual(len(calls), 0)

    def test_clean_stream_never_triggers(self) -> None:
        m, calls = _sync_monitor()
        for _ in range(20):
            m.record([])
        self.assertEqual(len(calls), 0)

    def test_cooldown_suppresses_immediate_re_escalation(self) -> None:
        m, calls = _sync_monitor()
        for f in [["x"], ["x"], ["x"]]:  # trigger on the 3rd
            m.record(f)
        self.assertEqual(len(calls), 1)
        # A full window of further flags must not re-escalate during cooldown.
        for _ in range(5):
            m.record(["x"])
        self.assertEqual(len(calls), 1)

    def test_scheduler_runs_off_request_path(self) -> None:
        # The default scheduler spawns a thread; here we prove the injected one is
        # what runs, and that record() returns the trigger boolean.
        ran = {"n": 0}
        m = EngineHealthMonitor(lambda: ran.__setitem__("n", ran["n"] + 1),
                                scheduler=lambda fn: fn())
        out = [m.record(["x"]) for _ in range(3)]
        self.assertEqual(out[-1], True)
        self.assertEqual(ran["n"], 1)


if __name__ == "__main__":
    unittest.main()
