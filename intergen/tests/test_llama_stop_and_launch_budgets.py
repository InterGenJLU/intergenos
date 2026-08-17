# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Serving self-recovery: stop() survives a child that will not die, and the
launch budget is derived from the model instead of guessed.

THE INCIDENT THIS GUARDS (measured 2026-08-13 18:39:06-18:41:24 on a dev box;
full journal capture kept with the change). The watchdog judged its own
llama-server unhealthy and stopped it. The child was wedged in an
uninterruptible kernel teardown (a GPU driver RPC wait reached through
do_exit -> drm_file_free), so:

  * SIGTERM did not land within the graceful window, stop() sent SIGKILL, and
    the following wait(timeout=5) RAISED subprocess.TimeoutExpired. Nothing
    caught it: the surrounding handler catches OSError, and TimeoutExpired is
    not one. The exception escaped stop(), escaped restart(), and surfaced as
    "Restart action failed: Command '[...llama-server...]' timed out after 5
    seconds" — so the first restart attempt never reached the launch at all.
  * The wall-clock reconciles exactly, which is how the cause was identified:
    SIGKILL at 18:39:16.744, failure logged at 18:39:31.745 = the 5s reap wait
    plus the 10s stderr-pump join that the finally block runs on the way out.

The message named a five-second timeout, so it reads like a launch that was not
given long enough to load a model. It was not: nothing in the launch path has a
five-second bound. This file pins both halves of the correction — the reap must
not raise, and the launch budget must be honest about model size.

WHAT "HONEST BUDGET" MEANS HERE. Both numbers are derived, not picked:
  * the post-kill reap wait is sized against the teardown the incident actually
    produced (the port stayed held across probes ending 98s after the kill);
  * the launch budget keeps the shipped 60s as a FLOOR and adds an allowance per
    gigabyte of model, so a model an order of magnitude larger than the one
    measured here cannot outgrow the budget silently.
"""
from __future__ import annotations

import subprocess
import unittest

from intergen import llama_manager
from intergen.llama_manager import LlamaManager

# The teardown the incident produced, in seconds: the port was still refusing a
# bind on the probe logged 98s after the SIGKILL (18:39:16 -> 18:40:54), and had
# released by the manual restart at 18:44:56. 98 is therefore a MEASURED LOWER
# BOUND on that teardown, not an estimate, and any wait that claims to outlast a
# GPU process's teardown has to clear it.
OBSERVED_TEARDOWN_FLOOR_S = 98.0

# The real serving pair on the box that produced the incident.
_MODEL_BYTES = 1_282_436_192      # InternVL3_5-2B-Q4_K_M.gguf
_MMPROJ_BYTES = 636_106_144       # mmproj-...-f16.gguf
# Measured load-to-healthy for that pair on that box, worst of three rounds
# (evidence 01-teardown-measurement.log): 6.53s.
_MEASURED_LOAD_S = 6.53


class _UnreapableChild:
    """A child that ignores every signal — the wedged-in-the-kernel case.

    wait() always times out, which is precisely what a process stuck in an
    uninterruptible kernel wait does to its parent, and poll() keeps saying
    "still running" because it is.
    """

    def __init__(self, pid: int = 424242):
        self.pid = pid
        self.stdout = None
        self.stderr = None
        self.returncode = None
        self.signals: list[int] = []
        self.killed = False
        self.wait_timeouts: list[float | None] = []

    def send_signal(self, sig):
        self.signals.append(sig)

    def kill(self):
        self.killed = True

    def poll(self):
        return None

    def wait(self, timeout=None):
        self.wait_timeouts.append(timeout)
        raise subprocess.TimeoutExpired(cmd=["/usr/bin/llama-server"],
                                        timeout=timeout or 0)


def _manager_with(child) -> LlamaManager:
    mgr = LlamaManager()
    mgr._process = child
    mgr._stderr_thread = None
    return mgr


class StopSurvivesAnUnreapableChild(unittest.TestCase):
    """stop() must always return. A raise here aborts the whole restart."""

    def test_stop_does_not_raise_when_the_child_never_reaps(self):
        """The regression in one line: this used to propagate TimeoutExpired.

        The exception did not merely log badly — it abandoned restart() before
        start_saved_config() was ever called, so the attempt was spent without
        one launch being tried.
        """
        child = _UnreapableChild()
        mgr = _manager_with(child)
        try:
            mgr.stop()
        except subprocess.TimeoutExpired as e:  # pragma: no cover - the defect
            self.fail(f"stop() propagated TimeoutExpired to its caller: {e}")
        self.assertTrue(child.killed, "the child was never SIGKILLed")

    def test_stop_says_plainly_that_the_child_did_not_reap(self):
        """A survivor that is never mentioned is a silent failure.

        The next start() has to deal with a process still holding the port, so
        the log must say the child outlived its kill rather than letting stop()
        look successful.
        """
        child = _UnreapableChild()
        mgr = _manager_with(child)
        with self.assertLogs(llama_manager.log, level="WARNING") as captured:
            mgr.stop()
        joined = "\n".join(captured.output).lower()
        self.assertIn("did not exit", joined,
                      f"stop() never reported the surviving child: {joined}")
        self.assertIn(str(child.pid), joined,
                      "the report does not name the surviving pid")

    def test_the_manager_lets_go_of_a_child_it_could_not_reap(self):
        """The handle is dropped either way, so the next start() runs its own
        ownership-verified reap path instead of trusting a stale handle."""
        mgr = _manager_with(_UnreapableChild())
        mgr.stop()
        self.assertIsNone(mgr._process)

    def test_the_post_kill_wait_outlasts_the_observed_teardown(self):
        """Behavioural, not a constant check: this asserts the timeout the code
        actually hands to wait() after the SIGKILL.

        Five seconds could not have succeeded against the teardown that was
        measured, so refusing at five seconds was never an honest verdict — it
        was a wait too short to have learned anything.
        """
        child = _UnreapableChild()
        mgr = _manager_with(child)
        mgr.stop()
        self.assertGreaterEqual(
            len(child.wait_timeouts), 2,
            "expected a graceful wait and a post-kill wait")
        post_kill = child.wait_timeouts[-1]
        self.assertIsNotNone(post_kill, "the post-kill wait is unbounded")
        self.assertGreaterEqual(
            post_kill, OBSERVED_TEARDOWN_FLOOR_S,
            f"the post-kill reap waits {post_kill}s, shorter than the {
                OBSERVED_TEARDOWN_FLOOR_S}s teardown this box actually produced")


class TheLaunchBudgetIsDerivedFromTheModel(unittest.TestCase):
    """A fixed launch budget is a constant a bigger model outgrows silently."""

    def test_the_budget_scales_with_the_bytes_being_loaded(self):
        small = llama_manager.startup_budget_seconds(_MODEL_BYTES)
        large = llama_manager.startup_budget_seconds(20 * 1024 ** 3)
        self.assertGreater(
            large, small,
            "a 20GB model gets no more time than a 1.2GB one — the budget is "
            "not a function of what is being loaded")

    def test_the_budget_never_drops_below_the_shipped_floor(self):
        """Nothing regresses: a tiny model keeps at least the 60s it had."""
        self.assertGreaterEqual(
            llama_manager.startup_budget_seconds(1024), 60.0)
        self.assertGreaterEqual(
            llama_manager.startup_budget_seconds(0), 60.0)

    def test_the_budget_covers_the_measured_load_with_real_headroom(self):
        """The pair measured on this hardware loaded in 6.53s at worst. The
        budget has to hold that on a cold cache and a slow disk, not just on the
        warm run that produced the number."""
        budget = llama_manager.startup_budget_seconds(
            _MODEL_BYTES + _MMPROJ_BYTES)
        self.assertGreaterEqual(
            budget, 10 * _MEASURED_LOAD_S,
            f"{budget}s leaves under 10x headroom on a {_MEASURED_LOAD_S}s "
            f"measured load")

    def test_a_thirty_five_billion_parameter_model_gets_minutes_not_seconds(self):
        """The silent-outgrow case the fixed 60s was heading for."""
        self.assertGreater(
            llama_manager.startup_budget_seconds(20 * 1024 ** 3), 300.0)

    def test_the_budget_is_what_the_health_wait_actually_uses(self):
        """The anti-mask pin: a derived budget nothing consults is decoration.

        _wait_for_healthy must poll against the derived deadline, so this drives
        the real function with a child that never answers and checks it waited
        the derived budget rather than the old flat constant.
        """
        clock = {"t": 1000.0}
        slept: list[float] = []

        def fake_time():
            return clock["t"]

        def fake_sleep(d):
            slept.append(d)
            clock["t"] += d

        mgr = LlamaManager()

        class _Running:
            pid = 999

            def poll(self):
                return None
        mgr._process = _Running()
        mgr._startup_budget = 400.0        # a big derived budget

        import unittest.mock as mock
        with mock.patch.object(llama_manager.time, "time", fake_time), \
                mock.patch.object(llama_manager.time, "sleep", fake_sleep), \
                mock.patch.object(llama_manager.urllib.request, "urlopen",
                                  side_effect=OSError("refused")):
            self.assertFalse(mgr._wait_for_healthy(65535))

        self.assertGreater(
            sum(slept), 300.0,
            f"the health wait gave up after {sum(slept)}s despite a 400s "
            f"derived budget — it is still using a fixed constant")


if __name__ == "__main__":
    unittest.main(verbosity=2)
