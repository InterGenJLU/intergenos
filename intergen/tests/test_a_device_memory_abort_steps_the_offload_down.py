# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An engine that runs out of device memory at a planned offload is started again on fewer layers.

THE DEFECT THIS PINS. When the engine died at load because the card ran out of memory, the start path retried the
SAME plan on every attempt and then on every watchdog restart, until the restart budget was spent: the plan that
had just been measured not to fit was relaunched each time, and the assistant never served. Measured on a 4 GB card:
seven such deaths per daemon start, each the same allocation failing at the same point.

THE RULE THESE CELLS HOLD. A start that dies with the engine's own words for exhausted DEVICE memory is started
again with fewer layers on the card: a plan that put every layer there is started with half of the model's layers,
and a plan that already put only some there is started on the processor. The step is logged with the engine's line
that showed it, it is taken inside the start's own attempts and inside one restart (so it spends neither), and the
plan that came up is the one later restarts relaunch. A layer count the configuration pins is honoured as written
and never stepped down. A failure that is not device memory (a build the driver cannot load, an absent model) is
not a reason to plan fewer layers and is handled exactly as before.

Hermetic: the start is a scripted stand-in recording the layer count it was asked for, except one cell that reads
a real child process's dying output through the health wait; no engine starts and no card is touched.
"""

from __future__ import annotations

import os
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from intergen import llama_manager, serving_device
from intergen.config import Config
from intergen.interfaces.types import StartFailure
from intergen.llama_manager import LlamaManager, ServerConfig

# The engine's own dying output on a 4 GB card with every layer of the 2B model planned onto it (the Vulkan build,
# 2026-10-08), the lines that matter, verbatim.
VULKAN_DEVICE_MEMORY_DEATH = """\
llama_params_fit_impl: projected to use 3143 MiB of device memory vs. 3937 MiB of free device memory
llama_params_fit_impl: cannot meet free memory target of 1024 MiB, need to reduce device memory by 229 MiB
load_tensors: offloaded 29/29 layers to GPU
load_tensors:      Vulkan0 model buffer size =  1050.43 MiB
llama_kv_cache:    Vulkan0 KV buffer size =  1792.00 MiB
sched_reserve:    Vulkan0 compute buffer size =   300.75 MiB
ggml_vulkan: Device memory allocation of size 636080128 failed.
ggml_vulkan: vk::Device::allocateMemory: ErrorOutOfDeviceMemory
alloc_tensor_range: failed to allocate Vulkan0 buffer of size 636080128
"""

# The CUDA build's death on a driver that cannot load its code (measured on the same machine): not device memory.
CUDA_TOOLCHAIN_DEATH = """\
ggml_cuda_init: found 1 CUDA devices:
CUDA error: the provided PTX was compiled with an unsupported toolchain.
  current device: 0, in function ggml_cuda_compute_forward at ggml-cuda.cu:2563
"""

UNITS = 29   # the 2B model: 28 blocks and the output layer


def _classify(text):
    function = getattr(llama_manager, "device_memory_failure_line", None)
    if function is None:
        raise AssertionError("llama_manager offers no reading of a device-memory death")
    return function(text)


class TheEnginesWordsForExhaustedDeviceMemoryAreRead(unittest.TestCase):

    def test_the_measured_vulkan_death_is_device_memory(self) -> None:
        line = _classify(VULKAN_DEVICE_MEMORY_DEATH)
        self.assertIsNotNone(line)
        self.assertIn("636080128", line)

    def test_the_cuda_and_hip_wordings_are_device_memory(self) -> None:
        for text in ("CUDA error: out of memory\n  current device: 0, in function alloc at x.cu:1\n",
                     "ggml_backend_cuda_buffer_type_alloc_buffer: allocating 1050.43 MiB on device 0: "
                     "cudaMalloc failed: out of memory\n",
                     "ROCm error: out of memory\n",
                     "alloc_tensor_range: failed to allocate CUDA0 buffer of size 1101453312\n"):
            with self.subTest(text=text.splitlines()[0]):
                self.assertIsNotNone(_classify(text))

    def test_a_build_the_driver_cannot_load_is_not_device_memory(self) -> None:
        self.assertIsNone(_classify(CUDA_TOOLCHAIN_DEATH))

    def test_a_host_memory_failure_is_not_device_memory(self) -> None:
        for text in ("alloc_tensor_range: failed to allocate CPU buffer of size 175000000\n",
                     "alloc_tensor_range: failed to allocate Vulkan_Host buffer of size 41953280\n",
                     "llama_init_from_model: failed to initialize the context\n"):
            with self.subTest(text=text.strip()):
                self.assertIsNone(_classify(text))

    def test_no_output_is_not_device_memory(self) -> None:
        self.assertIsNone(_classify(""))
        self.assertIsNone(_classify(None))


def _fake_binary(directory, name):
    path = Path(directory) / name
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _write_header(path: Path, blocks: int) -> None:
    """A GGUF version 3 header naming a qwen3 model of ``blocks`` blocks, no tensors."""
    def string(text: str) -> bytes:
        data = text.encode()
        return struct.pack("<Q", len(data)) + data
    body = (string("general.architecture") + struct.pack("<I", 8) + string("qwen3")
            + string("qwen3.block_count") + struct.pack("<I", 4) + struct.pack("<I", blocks))
    path.write_bytes(b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", 2) + body)


class _Harness(unittest.TestCase):
    """A real LlamaManager whose start is a scripted stand-in that records the layers it was asked for."""

    configured_layers = "auto"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="offload-step-down-")
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self._orig_paths = dict(serving_device.ENGINE_SERVER_PATHS)
        self.addCleanup(lambda: serving_device.ENGINE_SERVER_PATHS.update(self._orig_paths))
        for engine in list(serving_device.ENGINE_SERVER_PATHS):
            serving_device.ENGINE_SERVER_PATHS[engine] = str(tmp / f"absent-{engine}")
        self.vulkan = _fake_binary(tmp, "vulkan-server")
        serving_device.ENGINE_SERVER_PATHS["vulkan"] = self.vulkan
        self.model = tmp / "model.gguf"
        _write_header(self.model, UNITS - 1)
        real_get = Config.get
        configured = self

        def get(config, key, default=None):
            if key == "llama_server.gpu_layers":
                return configured.configured_layers
            return real_get(config, key, default)

        patch = mock.patch.object(Config, "get", get)
        patch.start()
        self.addCleanup(patch.stop)
        self.mgr = LlamaManager()
        self.mgr._config = ServerConfig(
            model_path=str(self.model), port=8080, context_size=16384, gpu_layers=999,
            parallel=1, jinja=False, reasoning="off", server_path=self.vulkan)
        self.attempts: list[int] = []

    def script(self, *, fits_at_most: int | None, death: str = VULKAN_DEVICE_MEMORY_DEATH) -> None:
        """Every start above ``fits_at_most`` layers dies with ``death``; one at or below it comes up."""
        def fake() -> bool:
            layers = self.mgr._config.gpu_layers
            self.attempts.append(layers)
            if fits_at_most is not None and min(layers, UNITS) <= fits_at_most:
                self.mgr._last_failure = StartFailure.NONE
                self.mgr._last_error = None
                self.mgr._exit_stderr = ""
                return True
            # What start() leaves behind when the engine died before answering /health.
            self.mgr._exit_stderr = death
            self.mgr._last_error = "Server failed to become healthy within timeout"
            self.mgr._last_failure = StartFailure.UNHEALTHY
            return False
        self.mgr.start_saved_config = fake


class TheStartPathStepsDown(_Harness):

    def test_every_layer_dying_for_device_memory_is_started_again_on_half(self) -> None:
        self.script(fits_at_most=20)
        with self.assertLogs("intergen.llama_manager", level="WARNING") as logs:
            started = self.mgr.retry_transient_start(attempts=3, sleep=lambda _s: None)
        self.assertTrue(started)
        self.assertEqual(self.attempts, [999, 14],
                         "the plan the card could not hold was started again instead of a smaller one")
        stepped = [r for r in logs.output if "device memory" in r]
        self.assertTrue(stepped, logs.output)
        self.assertIn("14 of 29", stepped[0])
        self.assertIn("Device memory allocation of size 636080128 failed", stepped[0])

    def test_half_still_dying_for_device_memory_is_started_on_the_processor(self) -> None:
        self.script(fits_at_most=0)
        self.assertTrue(self.mgr.retry_transient_start(attempts=3, sleep=lambda _s: None))
        self.assertEqual(self.attempts, [999, 14, 0])

    def test_a_partial_plan_dying_for_device_memory_is_started_on_the_processor(self) -> None:
        self.mgr._config = replace(self.mgr._config, gpu_layers=20)
        self.script(fits_at_most=0)
        self.assertTrue(self.mgr.retry_transient_start(attempts=3, sleep=lambda _s: None))
        self.assertEqual(self.attempts, [20, 0])

    def test_the_callers_own_first_start_is_stepped_down_before_the_retry(self) -> None:
        """The daemon's first start is its own call; the retry it then asks for must not repeat that plan."""
        self.script(fits_at_most=20)
        self.assertFalse(self.mgr.start_saved_config())
        self.assertTrue(self.mgr.retry_transient_start(attempts=3, sleep=lambda _s: None))
        self.assertEqual(self.attempts, [999, 14])

    def test_a_step_down_spends_no_attempt_and_no_back_off(self) -> None:
        self.script(fits_at_most=0)
        sleeps: list[float] = []
        self.assertTrue(self.mgr.retry_transient_start(attempts=1, sleep=sleeps.append))
        self.assertEqual(self.attempts, [999, 14, 0])
        self.assertEqual(sleeps, [])

    def test_the_stepped_plan_is_the_one_a_later_restart_relaunches(self) -> None:
        self.script(fits_at_most=20)
        self.assertTrue(self.mgr.retry_transient_start(attempts=3, sleep=lambda _s: None))
        self.mgr.stop = lambda: None
        self.assertTrue(self.mgr.restart())
        self.assertEqual(self.attempts, [999, 14, 14])

    def test_the_layer_count_falls_back_to_the_models_header(self) -> None:
        death = "\n".join(line for line in VULKAN_DEVICE_MEMORY_DEATH.splitlines()
                          if "offloaded" not in line)
        self.script(fits_at_most=20, death=death)
        self.assertTrue(self.mgr.retry_transient_start(attempts=3, sleep=lambda _s: None))
        self.assertEqual(self.attempts, [999, 14])

    def test_with_no_layer_count_anywhere_the_step_is_the_processor(self) -> None:
        death = "\n".join(line for line in VULKAN_DEVICE_MEMORY_DEATH.splitlines()
                          if "offloaded" not in line)
        self.mgr._config = replace(self.mgr._config,
                                   model_path=str(Path(self._tmp.name) / "no-header.gguf"))
        self.script(fits_at_most=20, death=death)
        self.assertTrue(self.mgr.retry_transient_start(attempts=3, sleep=lambda _s: None))
        self.assertEqual(self.attempts, [999, 0])


class TheRestartStepsDownInsideOneRestart(_Harness):

    def test_a_restart_dying_for_device_memory_comes_up_on_fewer_layers_in_the_same_restart(self) -> None:
        self.script(fits_at_most=20)
        self.mgr.stop = lambda: None
        self.assertTrue(self.mgr.restart())
        self.assertEqual(self.attempts, [999, 14])
        self.assertEqual(self.mgr._restart_count, 1, "one restart of the budget, not one per plan")


class WhatIsNotSteppedDown(_Harness):

    def test_a_failure_that_is_not_device_memory_repeats_the_plan_as_before(self) -> None:
        self.script(fits_at_most=None, death=CUDA_TOOLCHAIN_DEATH)
        self.assertFalse(self.mgr.retry_transient_start(attempts=3, sleep=lambda _s: None))
        self.assertEqual(self.attempts, [999, 999, 999])

    def test_a_processor_start_has_nothing_to_step_down(self) -> None:
        self.mgr._config = replace(self.mgr._config, gpu_layers=0)
        self.script(fits_at_most=None)
        self.assertFalse(self.mgr.retry_transient_start(attempts=2, sleep=lambda _s: None))
        self.assertEqual(self.attempts, [0, 0])


class APinnedLayerCountIsHonoured(_Harness):

    configured_layers = 999

    def test_a_pinned_count_dying_for_device_memory_is_not_stepped_down(self) -> None:
        self.script(fits_at_most=20)
        with self.assertLogs("intergen.llama_manager", level="WARNING") as logs:
            self.assertFalse(self.mgr.retry_transient_start(attempts=3, sleep=lambda _s: None))
        self.assertEqual(self.attempts, [999, 999, 999],
                         "the configured count is the operator's pin and is used as written")
        self.assertTrue(any("honoured as written" in r for r in logs.output), logs.output)


class TheHealthWaitKeepsADeadChildsOutput(unittest.TestCase):

    def test_the_dying_output_is_kept_for_the_step_down(self) -> None:
        mgr = LlamaManager()
        child = subprocess.Popen(
            [sys.executable, "-I", "-c",
             "import sys; sys.stderr.write(sys.argv[1]); sys.exit(1)", VULKAN_DEVICE_MEMORY_DEATH],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={"PATH": os.environ.get("PATH", "")})
        child.wait(timeout=30)
        mgr._process = child
        mgr._startup_budget = 5
        try:
            self.assertFalse(mgr._wait_for_healthy(port=9))
            kept = getattr(mgr, "_exit_stderr", None)
            self.assertIsNotNone(kept, "the health wait kept nothing of the dead child's output")
            self.assertIn("ErrorOutOfDeviceMemory", kept)
        finally:
            for stream in (child.stdout, child.stderr):
                if stream:
                    stream.close()


if __name__ == "__main__":
    unittest.main()
