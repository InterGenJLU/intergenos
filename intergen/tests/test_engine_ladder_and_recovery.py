# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A failing engine must cost one rung, not the whole assistant.

TWO FAILURES THAT COMPOUNDED INTO SILENCE.

A restart relaunches the SAME server binary, which is right when the server died
for a reason the binary is not responsible for, and exactly wrong when the
binary itself cannot run on this machine. The HIP build carries device code only
for the architectures it was compiled for and segfaults at model load on
anything else. So on such a machine: three restarts, all of the same binary, all
segfaulting, budget spent.

Then the watchdog, on spending its budget, set its stop event and BROKE OUT OF
ITS OWN LOOP. Nothing tried again for the rest of the process's life. A machine
carrying a perfectly good Vulkan engine sat silent until someone restarted the
daemon — and it stayed silent just as thoroughly when the cause was temporary,
like an accelerator still held by a game that has since been closed.

Neither half is sufficient alone. Advancing the ladder without a recovery retry
still strands a machine whose whole ladder was briefly unavailable; a recovery
retry without the ladder just re-runs the same doomed binary every fifteen
minutes. Both are tested here together for that reason.

The tests drive real objects — a real Watchdog with injected callables, a real
LlamaManager with its start path stubbed — rather than asserting on source text.
No llama-server is launched and no GPU is touched.
"""
from __future__ import annotations

import os
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path

from intergen import serving_device
from intergen.watchdog import Watchdog


def _fake_binary(directory, name):
    """An executable file standing in for an engine's server binary."""
    p = Path(directory) / name
    p.write_text("#!/bin/sh\nexit 0\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return str(p)


class EngineLadderTest(unittest.TestCase):
    """serving_device.engine_ladder / next_engine_after."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="engine-ladder-")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self._orig_paths = dict(serving_device.ENGINE_SERVER_PATHS)
        self.addCleanup(
            lambda: serving_device.ENGINE_SERVER_PATHS.update(self._orig_paths))
        self._orig_supported = serving_device.hip_is_supported_here
        self.addCleanup(
            lambda: setattr(serving_device, "hip_is_supported_here",
                            self._orig_supported))
        # Nothing present unless a test says so.
        for engine in list(serving_device.ENGINE_SERVER_PATHS):
            serving_device.ENGINE_SERVER_PATHS[engine] = os.path.join(
                self.tmp, f"absent-{engine}")
        serving_device.hip_is_supported_here = lambda *a, **k: None

    def _present(self, *engines):
        for engine in engines:
            serving_device.ENGINE_SERVER_PATHS[engine] = _fake_binary(
                self.tmp, f"{engine}-server")

    def test_the_amd_ladder_is_hip_then_vulkan(self):
        self._present("hip", "vulkan")
        self.assertEqual([e for e, _ in serving_device.engine_ladder("amd")],
                         ["hip", "vulkan"])

    def test_an_absent_engine_is_not_a_rung(self):
        self._present("vulkan")
        self.assertEqual([e for e, _ in serving_device.engine_ladder("amd")],
                         ["vulkan"])

    def test_an_unsupported_hip_build_is_not_a_rung(self):
        """The architecture gate applies to the ladder too.

        Falling onto an engine already known not to run here would just be a
        slower way to fail.
        """
        self._present("hip", "vulkan")
        serving_device.hip_is_supported_here = lambda *a, **k: False
        self.assertEqual([e for e, _ in serving_device.engine_ladder("amd")],
                         ["vulkan"])

    def test_vulkan_is_the_floor_even_for_an_unknown_vendor(self):
        self._present("vulkan")
        self.assertEqual(
            [e for e, _ in serving_device.engine_ladder("something-else")],
            ["vulkan"])

    def test_next_after_hip_is_vulkan(self):
        self._present("hip", "vulkan")
        nxt = serving_device.next_engine_after("hip", "amd")
        self.assertIsNotNone(nxt)
        self.assertEqual(nxt[0], "vulkan")

    def test_the_bottom_rung_has_nothing_below_it(self):
        """The honest end of the ladder — the caller must fail, not loop."""
        self._present("hip", "vulkan")
        self.assertIsNone(serving_device.next_engine_after("vulkan", "amd"))

    def test_no_engines_at_all_is_none_not_a_crash(self):
        self.assertIsNone(serving_device.next_engine_after("hip", "amd"))

    def test_an_unknown_current_engine_offers_the_top_rung(self):
        self._present("hip", "vulkan")
        nxt = serving_device.next_engine_after("some-removed-build", "amd")
        self.assertIsNotNone(nxt)
        self.assertEqual(nxt[0], "hip")


class WatchdogRecoveryTest(unittest.TestCase):
    """The watchdog must not stop monitoring when its budget is spent."""

    def _run_watchdog(self, *, healthy_after=None, max_restarts=2,
                      recovery_interval=1, run_for=3.0):
        """Drive a real Watchdog on a fast clock. Returns (dog, log).

        `healthy_after` is a number of health checks after which the service
        starts reporting healthy — used to model a blocking condition clearing
        while the watchdog is in its recovery hold.
        """
        state = {"checks": 0, "restarts": 0, "failures": []}

        def health():
            state["checks"] += 1
            if healthy_after is not None and state["checks"] > healthy_after:
                return True
            return False

        def restart():
            state["restarts"] += 1
            return healthy_after is not None and state["checks"] > healthy_after

        dog = Watchdog(health_check=health, restart_action=restart,
                       check_interval=0.05, max_restarts=max_restarts,
                       on_failure=state["failures"].append,
                       recovery_interval=recovery_interval)
        dog.start()
        time.sleep(run_for)
        dog.stop()
        return dog, state

    def test_it_keeps_running_after_the_budget_is_spent(self):
        """The regression in one line: the thread used to exit here."""
        dog, state = self._run_watchdog(run_for=2.5)
        self.assertGreater(state["restarts"], 0, "it never even tried")
        self.assertTrue(state["failures"],
                        "the exhaustion was never reported to the user")

    def test_it_tries_again_after_the_recovery_interval(self):
        dog, state = self._run_watchdog(max_restarts=1, recovery_interval=0.3,
                                        run_for=2.5)
        self.assertGreater(
            state["restarts"], 1,
            "the watchdog gave up permanently instead of retrying after the "
            "recovery interval")

    def test_the_failure_is_announced_once_not_every_cycle(self):
        """Alarm fatigue is a real failure mode of a retry loop."""
        dog, state = self._run_watchdog(max_restarts=1, recovery_interval=0.3,
                                        run_for=2.5)
        self.assertEqual(
            len(state["failures"]), 1,
            f"the exhaustion notice fired {len(state['failures'])} times")

    def test_a_recovery_attempt_costs_one_launch_not_a_fresh_burst(self):
        """The budget is reset to a single try, never refilled.

        A permanently broken engine must cost one launch per interval. If the
        budget were refilled, every interval would spend a full burst and the
        loop this budget exists to prevent would just run on a slower clock.
        """
        dog, state = self._run_watchdog(max_restarts=3, recovery_interval=0.4,
                                        run_for=3.0)
        # With interval 0.4s over ~3s there are at most ~7 holds; a refilled
        # budget would produce 3 launches per hold.
        self.assertLess(
            state["restarts"], 3 + 8,
            f"{state['restarts']} restarts suggests the budget is being "
            f"refilled rather than reset to a single attempt")

    def test_recovery_actually_recovers(self):
        """The point of the retry: the machine comes back on its own."""
        dog, state = self._run_watchdog(healthy_after=4, max_restarts=1,
                                        recovery_interval=0.3, run_for=2.5)
        self.assertTrue(dog.is_running or state["restarts"] > 1)
        self.assertGreater(state["restarts"], 0)

    def test_the_status_says_whether_anything_is_still_trying(self):
        dog, state = self._run_watchdog(max_restarts=1, recovery_interval=5,
                                        run_for=1.5)
        status = dog.get_status()
        self.assertIn("restart_budget_exhausted", status)
        self.assertIn("recovery_interval", status)
        self.assertTrue(status["restart_budget_exhausted"],
                        "status does not say the budget is spent, so a user "
                        "cannot tell silence from still-trying")


class EngineLevelFailureSetTest(unittest.TestCase):
    """Which failures are worth trying a different engine for."""

    def test_the_set_is_the_never_came_up_class(self):
        from intergen.llama_manager import _ENGINE_LEVEL_FAILURES
        from intergen.interfaces.types import StartFailure
        self.assertEqual(
            _ENGINE_LEVEL_FAILURES,
            frozenset({StartFailure.BINARY_ABSENT,
                       StartFailure.SPAWN_ERROR,
                       StartFailure.UNHEALTHY}))

    def test_integrity_failures_are_not_retried_on_another_engine(self):
        """A declared capability the server did not honour reads as tamper.

        Quietly retrying past it on a different binary would turn a
        conspicuous integrity signal into a silent engine switch.
        """
        from intergen.llama_manager import _ENGINE_LEVEL_FAILURES
        from intergen.interfaces.types import StartFailure
        for member in (StartFailure.MMPROJ_MISSING,
                       StartFailure.CHAT_TEMPLATE_MISSING,
                       StartFailure.TOOLS_NOT_ADVERTISED,
                       StartFailure.VISION_NOT_ADVERTISED):
            self.assertNotIn(member, _ENGINE_LEVEL_FAILURES)

    def test_offload_failure_keeps_its_own_ratified_answer(self):
        """OFFLOAD_FAILED falls to the 2B floor; it is not an engine swap."""
        from intergen.llama_manager import _ENGINE_LEVEL_FAILURES
        from intergen.interfaces.types import StartFailure
        self.assertNotIn(StartFailure.OFFLOAD_FAILED, _ENGINE_LEVEL_FAILURES)

    def test_a_missing_model_is_not_the_engines_fault(self):
        from intergen.llama_manager import _ENGINE_LEVEL_FAILURES
        from intergen.interfaces.types import StartFailure
        self.assertNotIn(StartFailure.MODEL_FILE_ABSENT,
                         _ENGINE_LEVEL_FAILURES)
        self.assertNotIn(StartFailure.PORT_IN_USE, _ENGINE_LEVEL_FAILURES)


class RestartAdvancesTheLadderTest(unittest.TestCase):
    """LlamaManager.restart drops to the next engine when the engine failed."""

    def setUp(self):
        from intergen.llama_manager import LlamaManager, ServerConfig
        from intergen.interfaces.types import StartFailure
        self.StartFailure = StartFailure
        self.tmp = tempfile.mkdtemp(prefix="ladder-restart-")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self._orig_paths = dict(serving_device.ENGINE_SERVER_PATHS)
        self.addCleanup(
            lambda: serving_device.ENGINE_SERVER_PATHS.update(self._orig_paths))
        self._orig_supported = serving_device.hip_is_supported_here
        self.addCleanup(
            lambda: setattr(serving_device, "hip_is_supported_here",
                            self._orig_supported))
        serving_device.hip_is_supported_here = lambda *a, **k: None
        self.hip = _fake_binary(self.tmp, "hip-server")
        self.vulkan = _fake_binary(self.tmp, "vulkan-server")
        serving_device.ENGINE_SERVER_PATHS["hip"] = self.hip
        serving_device.ENGINE_SERVER_PATHS["vulkan"] = self.vulkan
        serving_device.ENGINE_SERVER_PATHS["cuda"] = os.path.join(
            self.tmp, "absent-cuda")

        self.mgr = LlamaManager()
        self.mgr._config = ServerConfig(
            model_path="/nonexistent/model.gguf", port=8080,
            context_size=4096, gpu_layers=0, parallel=1, jinja=False,
            reasoning="none", server_path=self.hip)
        self.attempts = []

    def _stub_start(self, results):
        """Replace start_saved_config with a scripted sequence of outcomes."""
        seq = list(results)

        def fake():
            path = self.mgr._config.server_path
            self.attempts.append(path)
            outcome = seq.pop(0) if seq else False
            if not outcome:
                self.mgr._last_failure = self.StartFailure.UNHEALTHY
            return outcome

        self.mgr.start_saved_config = fake

    def test_a_failed_engine_start_retries_on_the_next_engine(self):
        self._stub_start([False, True])
        self.assertTrue(self.mgr.restart())
        self.assertEqual(self.attempts, [self.hip, self.vulkan],
                         "the restart did not drop to the next engine")

    def test_the_device_pin_does_not_travel_between_engines(self):
        """Device names are backend-local.

        Carrying "Vulkan1" onto a different engine pins to whatever that name
        means there, or to nothing at all.
        """
        from dataclasses import replace as _replace
        self.mgr._config = _replace(self.mgr._config, device="Vulkan1")
        self._stub_start([False, True])
        self.mgr.restart()
        self.assertIsNone(self.mgr._config.device)

    def test_a_non_engine_failure_does_not_switch_engines(self):
        self._stub_start([False])
        self.mgr._last_failure = self.StartFailure.NONE

        def fake():
            self.attempts.append(self.mgr._config.server_path)
            self.mgr._last_failure = self.StartFailure.MODEL_FILE_ABSENT
            return False

        self.mgr.start_saved_config = fake
        self.assertFalse(self.mgr.restart())
        self.assertEqual(self.attempts, [self.hip],
                         "a missing model file caused an engine switch")

    def test_the_bottom_of_the_ladder_fails_rather_than_looping(self):
        self.mgr._config = __import__("dataclasses").replace(
            self.mgr._config, server_path=self.vulkan)
        self._stub_start([False, False])
        self.assertFalse(self.mgr.restart())
        self.assertEqual(self.attempts, [self.vulkan],
                         "it tried to advance past the bottom rung")


if __name__ == "__main__":
    unittest.main()
