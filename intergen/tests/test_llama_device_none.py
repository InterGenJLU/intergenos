# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""CPU-resident instances must never initialize an accelerator device (F24).

Regression guard: llama.cpp's Vulkan backend enumerates and touches the GPU
device even at --n-gpu-layers 0, so on a broken/unstable Vulkan ICD (the
observed case: NVK on GA104 — vk::DeviceLostError during device init, the
nouveau GSP fault class) a CPU-pinned server ABORTS at startup. The daemon then
reads a health timeout and leaves the engine down with a perfectly healthy
model on disk — the panel icon never appears despite onboarding reporting
ready. The fix passes --device none for gpu_layers == 0 instances, skipping
device init entirely; GPU instances (gpu_layers > 0) keep their flags
unchanged.

These tests pin the command construction only: subprocess.Popen is replaced
with a recorder that captures argv and aborts the launch, so no server, model
load, or device is ever touched.
"""
from __future__ import annotations

import contextlib
import socket
import tempfile

from intergen import llama_manager
from intergen.llama_manager import LlamaManager


class _CmdRecorder:
    """Popen stand-in: record argv, then abort the launch."""

    last_cmd: list[str] | None = None

    def __init__(self, cmd, **_kwargs):
        _CmdRecorder.last_cmd = list(cmd)
        raise RuntimeError("test sentinel: stop after cmd construction")


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _build_cmd(gpu_layers: int) -> list[str]:
    """Drive start() far enough to capture the constructed argv."""
    _CmdRecorder.last_cmd = None
    real_popen = llama_manager.subprocess.Popen
    llama_manager.subprocess.Popen = _CmdRecorder
    try:
        with tempfile.NamedTemporaryFile(suffix=".gguf") as model:
            mgr = LlamaManager()
            with contextlib.suppress(Exception):
                mgr.start(
                    model.name,
                    port=_free_port(),
                    gpu_layers=gpu_layers,
                    embedding=(gpu_layers == 0),
                )
    finally:
        llama_manager.subprocess.Popen = real_popen
    assert _CmdRecorder.last_cmd is not None, (
        "start() never reached command construction — a pre-launch gate "
        "failed; the test environment is wrong, not the fix"
    )
    return _CmdRecorder.last_cmd


def test_cpu_resident_instance_passes_device_none():
    cmd = _build_cmd(gpu_layers=0)
    assert "--device" in cmd, f"--device missing from CPU-instance argv: {cmd}"
    assert cmd[cmd.index("--device") + 1] == "none"


def test_gpu_instance_does_not_pass_device_none():
    cmd = _build_cmd(gpu_layers=999)
    assert "--device" not in cmd, (
        f"--device must not appear for GPU instances (gpu_layers owns "
        f"offload): {cmd}"
    )
