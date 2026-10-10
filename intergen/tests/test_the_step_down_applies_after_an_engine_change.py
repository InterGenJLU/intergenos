# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Inside one restart, a device-memory death gets the same bounded smaller-plan
step on the next engine as on the first.

THE DEFECT THIS PINS. A restart whose relaunch died because the card ran out
of memory started again on fewer layers inside the same restart, but only on
the engine the restart began with. When that engine could not run at all and
the restart moved to the next engine, the next engine's start was the
restart's last word: if it died for device memory, the restart ended on the
plan that had just been measured not to fit, and only a later restart, from
the caller's budget, could reach the smaller plan.

THE CELLS are the reader's three shapes on the exact methods, with the engine
starts scripted: the first engine fails for a reason that is not memory and
the next dies for device memory with every layer and fits with half (the
target); the first engine dies for device memory and fits with half (the
first comparison); the first fails for a reason that is not memory and the
next comes up as planned (the second comparison).
"""
from __future__ import annotations

import unittest
from dataclasses import replace

from intergen.interfaces.types import StartFailure
from intergen.tests.test_a_device_memory_abort_steps_the_offload_down import (
    CUDA_TOOLCHAIN_DEATH, VULKAN_DEVICE_MEMORY_DEATH, _Harness)


class _TwoEngines(_Harness):
    """The restart begins on the first engine; one engine change is
    available, to the second."""

    def two_engines(self, first: tuple, second: tuple) -> list:
        """Each engine is (death, fits_at_most): a start above fits_at_most
        layers dies with death; at or below it, it comes up."""
        starts: list = []
        plans = {"first": first, "second": second}

        def engine_now() -> str:
            return ("second" if self.mgr._config.server_path == "second"
                    else "first")

        def advance() -> bool:
            if engine_now() == "second":
                return False
            self.mgr._config = replace(self.mgr._config, server_path="second")
            return True

        def start() -> bool:
            engine, layers = engine_now(), self.mgr._config.gpu_layers
            starts.append([engine, layers])
            death, fits = plans[engine]
            if fits is not None and min(layers, 29) <= fits:
                self.mgr._last_failure = StartFailure.NONE
                self.mgr._last_error = None
                self.mgr._exit_stderr = ""
                return True
            self.mgr._exit_stderr = death
            self.mgr._last_error = "the engine died before it answered"
            self.mgr._last_failure = StartFailure.UNHEALTHY
            return False

        self.mgr.start_saved_config = start
        self.mgr._advance_engine = advance
        self.mgr.stop = lambda: None
        return starts


class TheStepDownAppliesAfterAnEngineChange(_TwoEngines):

    def test_the_next_engine_dying_for_device_memory_comes_up_on_half(self):
        starts = self.two_engines(first=(CUDA_TOOLCHAIN_DEATH, None),
                                  second=(VULKAN_DEVICE_MEMORY_DEATH, 14))
        self.assertTrue(self.mgr.restart())
        self.assertEqual(starts, [["first", 999], ["second", 999],
                                  ["second", 14]])
        self.assertEqual(self.mgr._config.gpu_layers, 14)
        self.assertEqual(self.mgr._restart_count, 1)


class TheComparisons(_TwoEngines):

    def test_the_first_engine_dying_for_device_memory_comes_up_on_half(self):
        starts = self.two_engines(first=(VULKAN_DEVICE_MEMORY_DEATH, 14),
                                  second=(CUDA_TOOLCHAIN_DEATH, None))
        self.assertTrue(self.mgr.restart())
        self.assertEqual(starts, [["first", 999], ["first", 14]])
        self.assertEqual(self.mgr._restart_count, 1)

    def test_the_next_engine_coming_up_as_planned_is_not_stepped_down(self):
        starts = self.two_engines(first=(CUDA_TOOLCHAIN_DEATH, None),
                                  second=(VULKAN_DEVICE_MEMORY_DEATH, 999))
        self.assertTrue(self.mgr.restart())
        self.assertEqual(starts, [["first", 999], ["second", 999]])
        self.assertEqual(self.mgr._restart_count, 1)


if __name__ == "__main__":
    unittest.main()
