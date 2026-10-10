# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Each attempt the chat watchdog's recovery hold grants launches the engine.

THE DEFECT THIS PINS. Once the chat watchdog has spent its three restarts it
holds, says in the log that it will try again every fifteen minutes, and
after each hold resets its own budget to one attempt. The engine manager
counts its restarts too, and only a healthy start resets that count, so
every attempt a hold granted was refused by the manager before any launch.
Measured on a machine whose engine could not start: 76 attempts over a night,
76 refusals, no launch, and the log promising a retry each time.

THE RULE THESE CELLS HOLD. An attempt the hold grants is granted by the
manager's own count too, so it launches; the three restarts of a burst are
counted as before, and a caller restarting in a loop without a hold is still
refused after three.

The watchdog and the daemon's restart action are the real ones, with short
intervals; the engine start is a stand-in that fails for a reason that
neither moves to another engine nor plans fewer layers.
"""
from __future__ import annotations

import threading
import time
import unittest

from intergen import dbus_daemon as dd
from intergen.interfaces.types import StartFailure
from intergen.llama_manager import MAX_RESTART_ATTEMPTS, LlamaManager, ServerConfig
from intergen.watchdog import Watchdog

DEADLINE = 15.0


def _failing_manager() -> tuple[LlamaManager, list]:
    """A manager whose every start fails, recording each launch."""
    mgr = LlamaManager()
    mgr._config = ServerConfig(
        model_path="/stand-in/model.gguf", port=18125, context_size=4096,
        gpu_layers=0, parallel=1, jinja=False, reasoning="off")
    launches: list[float] = []

    def start() -> bool:
        launches.append(time.monotonic())
        mgr._last_error = "the stand-in engine did not start"
        mgr._last_failure = StartFailure.PORT_IN_USE
        mgr._exit_stderr = ""
        return False

    mgr.start_saved_config = start
    mgr.stop = lambda: None
    return mgr, launches


class ARecoveryHoldRelaunchesTheEngine(unittest.TestCase):

    def test_the_holds_attempt_after_three_failed_restarts_launches(self):
        mgr, launches = _failing_manager()
        daemon = dd.InterGenDaemon.__new__(dd.InterGenDaemon)
        daemon._pause_lock = threading.Lock()
        daemon._paused = False
        daemon._pause_holds = []
        daemon._llama = mgr
        daemon._watchdog = Watchdog(
            health_check=lambda: False,
            restart_action=daemon._restart_chat_server,
            check_interval=0.01, max_restarts=MAX_RESTART_ATTEMPTS,
            recovery_interval=0.05)
        daemon._watchdog.start()
        try:
            deadline = time.monotonic() + DEADLINE
            while (time.monotonic() < deadline
                   and len(launches) < MAX_RESTART_ATTEMPTS + 2):
                time.sleep(0.01)
        finally:
            daemon._watchdog.stop()
        self.assertGreaterEqual(
            len(launches), MAX_RESTART_ATTEMPTS + 2,
            f"{len(launches)} launches in {DEADLINE} s: after the burst of "
            f"{MAX_RESTART_ATTEMPTS} the recovery hold's attempts launched "
            f"nothing")
        self.assertTrue(daemon._watchdog.exhausted)


class TheManagersOwnBudgetStillHolds(unittest.TestCase):

    def test_a_restart_in_a_loop_without_a_hold_is_refused_after_three(self):
        mgr, launches = _failing_manager()
        results = [mgr.restart() for _ in range(MAX_RESTART_ATTEMPTS + 2)]
        self.assertEqual(results, [False] * (MAX_RESTART_ATTEMPTS + 2))
        self.assertEqual(len(launches), MAX_RESTART_ATTEMPTS)

    def test_a_granted_attempt_launches_once_and_no_more(self):
        mgr, launches = _failing_manager()
        for _ in range(MAX_RESTART_ATTEMPTS):
            mgr.restart()
        grant = getattr(mgr, "allow_one_restart", None)
        self.assertIsNotNone(grant, "the manager offers no way to grant the "
                                    "hold's attempt")
        grant()
        mgr.restart()
        mgr.restart()
        self.assertEqual(len(launches), MAX_RESTART_ATTEMPTS + 1)


if __name__ == "__main__":
    unittest.main()
