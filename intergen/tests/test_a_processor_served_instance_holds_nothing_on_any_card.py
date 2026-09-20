# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A processor-served instance holds nothing on any card.

Hiding the cards from the engine was not enough. Measured on a two-card AMD
machine on 2026-09-20, on the running daemon: the embedding instance — started
with ``--n-gpu-layers 0``, ``--device none`` and an empty ROCR_VISIBLE_DEVICES,
exactly as the previous release ships it — still held /dev/kfd and both render
nodes open, with 28 KiB of video memory and 2088 KiB of system memory charged
to each card. Adding HIP_VISIBLE_DEVICES to the same launch changed none of
those numbers. The ROCm engine links its GPU runtime as a direct library
dependency, so the runtime starts with the process, before any flag or variable
can reach it.

The same launch through the base engine, with the Vulkan loader told to open no
driver, held no device handle at all and no memory on either card, and still
returned a correct 768-value embedding.

These tests pin that: a launch with zero layers is served by the base engine
with the loader switch set, whatever engine it was handed; and the cases that
must not change — a launch that puts layers on a card, and a machine with no
base engine installed — stay as they were.
"""
import contextlib
import os
import socket
import tempfile

from intergen import llama_manager, serving_device
from intergen.llama_manager import LlamaManager
from intergen.serving_device import (
    NO_CARD_AT_ALL, NO_GRAPHICS_DRIVER_AT_ALL, ROCR_VISIBLE_DEVICES,
    VK_LOADER_DRIVERS_DISABLE, engine_that_opens_no_card)


class _LaunchRecorder:
    """Stands in for Popen and keeps the argv and environment of the launch."""
    last_cmd = None
    last_env = None
    real_popen = None

    def __new__(cls, cmd, **kwargs):
        argv = list(cmd)
        if "--model" not in argv:
            return cls.real_popen(argv, **kwargs)
        cls.last_cmd = argv
        cls.last_env = kwargs.get("env")
        raise RuntimeError("test sentinel: stop after cmd construction")


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@contextlib.contextmanager
def _engines(*, base_installed=True, base_lists_devices=False,
             base_refuses_to_answer=False):
    """Executable stand-ins for the engine builds, at the recipe paths.

    Yields ``(hip_path, base_path)``. With ``base_installed`` false the base
    engine path names a file that does not exist, which is the machine this
    change cannot help and must leave alone.

    With ``base_lists_devices`` the stand-in base engine PRINTS a device even
    with the loader switch set, which is what a graphics loader too old to know
    the switch produces. With ``base_refuses_to_answer`` it exits non-zero and
    prints nothing usable, standing for an engine that cannot be asked.
    """
    with tempfile.TemporaryDirectory() as tmp:
        hip = os.path.join(tmp, "rocm-llama-server")
        base = os.path.join(tmp, "base-llama-server")
        for path in (hip, base):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\nexit 0\n")
            os.chmod(path, 0o755)
        if base_lists_devices:
            with open(base, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\n"
                         "echo 'Available devices:'\n"
                         "echo '  Vulkan0: A Card (8176 MiB, 8116 MiB free) "
                         "[PCI 0000:06:00.0]'\n"
                         "exit 0\n")
            os.chmod(base, 0o755)
        if base_refuses_to_answer:
            # A real, executable file whose interpreter does not exist: it
            # passes the is-it-installed check and then fails to start, which
            # is an engine that cannot be asked rather than one that is absent.
            with open(base, "w", encoding="utf-8") as fh:
                fh.write("#!/nonexistent/interpreter\n")
            os.chmod(base, 0o755)
        if not base_installed:
            os.remove(base)
        saved_hip = serving_device.ENGINE_SERVER_PATHS["hip"]
        saved_base = serving_device.ENGINE_SERVER_PATHS["vulkan"]
        serving_device.ENGINE_SERVER_PATHS["hip"] = hip
        serving_device.ENGINE_SERVER_PATHS["vulkan"] = base
        try:
            yield hip, base
        finally:
            serving_device.ENGINE_SERVER_PATHS["hip"] = saved_hip
            serving_device.ENGINE_SERVER_PATHS["vulkan"] = saved_base


def _launch(*, gpu_layers, handed_engine, device=None, device_pci=None,
            base_installed=True, base_lists_devices=False):
    """Run start() far enough to capture the argv and environment it built."""
    _LaunchRecorder.last_cmd = None
    _LaunchRecorder.last_env = None
    real_popen = llama_manager.subprocess.Popen
    _LaunchRecorder.real_popen = real_popen
    llama_manager.subprocess.Popen = _LaunchRecorder
    try:
        with tempfile.NamedTemporaryFile(suffix=".gguf") as model, \
                _engines(base_installed=base_installed,
                         base_lists_devices=base_lists_devices) as (hip, base):
            server = hip if handed_engine == "hip" else base
            mgr = LlamaManager()
            with contextlib.suppress(Exception):
                mgr.start(model.name, port=_free_port(), gpu_layers=gpu_layers,
                          device=device, device_pci=device_pci,
                          embedding=(gpu_layers == 0), server_path=server)
    finally:
        llama_manager.subprocess.Popen = real_popen
    assert _LaunchRecorder.last_cmd is not None, (
        "start() never reached command construction — a pre-launch gate "
        "failed; the test environment is wrong, not the change")
    return _LaunchRecorder.last_cmd, _LaunchRecorder.last_env


def test_a_processor_served_instance_is_served_by_the_base_engine():
    cmd, env = _launch(gpu_layers=0, handed_engine="hip",
                       device="ROCm1", device_pci="0000:0e:00.0")
    assert cmd[0] == serving_device.ENGINE_SERVER_PATHS["vulkan"] or \
        os.path.basename(cmd[0]) == "base-llama-server", (
            "a launch with zero layers was served by an engine that links a "
            "GPU runtime it cannot avoid initialising, so it opens every card "
            f"and holds memory on each: {cmd[0]}")
    assert cmd[cmd.index("--device") + 1] == "none", cmd


def test_a_processor_served_instance_tells_the_loader_to_open_no_driver():
    cmd, env = _launch(gpu_layers=0, handed_engine="hip",
                       device="ROCm1", device_pci="0000:0e:00.0")
    assert env is not None, (
        "the launch inherited the daemon's environment unchanged, so the "
        "graphics loader opens a driver and the instance holds memory on "
        "every card")
    assert env.get(VK_LOADER_DRIVERS_DISABLE) == NO_GRAPHICS_DRIVER_AT_ALL, env
    assert env.get("PATH") == os.environ.get("PATH"), (
        "the environment must be ADDED to the daemon's, not replace it")


def test_a_processor_served_instance_already_on_the_base_engine_still_gets_the_switch():
    cmd, env = _launch(gpu_layers=0, handed_engine="vulkan")
    assert env is not None and \
        env.get(VK_LOADER_DRIVERS_DISABLE) == NO_GRAPHICS_DRIVER_AT_ALL, env
    assert os.path.basename(cmd[0]) == "base-llama-server", cmd


def test_with_no_base_engine_installed_the_launch_is_left_as_it_was():
    cmd, env = _launch(gpu_layers=0, handed_engine="hip",
                       device="ROCm1", device_pci="0000:0e:00.0",
                       base_installed=False)
    assert os.path.basename(cmd[0]) == "rocm-llama-server", (
        "with no base engine installed the launch must keep the engine it "
        "was given")
    assert env is not None and env.get(ROCR_VISIBLE_DEVICES) == NO_CARD_AT_ALL, (
        "the filter that release already applied must still be applied")
    assert VK_LOADER_DRIVERS_DISABLE not in env, (
        "a switch the ROCm engine does not read must not be set for it")


def test_a_launch_that_puts_layers_on_a_card_is_untouched():
    cmd, env = _launch(gpu_layers=999, handed_engine="hip",
                       device=None, device_pci=None)
    assert os.path.basename(cmd[0]) == "rocm-llama-server", (
        "a launch that serves on a card must keep the engine chosen for it")
    assert env is None or VK_LOADER_DRIVERS_DISABLE not in env, (
        "a launch that serves on a card must never be told to open no "
        "graphics driver")


def test_the_chooser_reports_a_missing_base_engine_as_a_sentence():
    with _engines(base_installed=False) as (hip, base):
        answer = engine_that_opens_no_card(hip)
    assert isinstance(answer, str), answer
    assert base in answer, (
        "the sentence must name the path that was looked for, so a log line "
        "says which engine is missing")


def test_the_chooser_returns_the_base_engine_and_the_loader_switch():
    with _engines() as (hip, base):
        answer = engine_that_opens_no_card(hip)
    assert not isinstance(answer, str), answer
    path, env, reason = answer
    assert path == base
    assert env == {VK_LOADER_DRIVERS_DISABLE: NO_GRAPHICS_DRIVER_AT_ALL}
    assert reason, "the choice must carry a reason for the log"


def test_a_loader_that_ignores_the_switch_is_refused_rather_than_trusted():
    """A graphics loader too old to know the switch opens its drivers anyway
    and still lists the cards. Launching under it would leave the whole residue
    and say nothing, so the choice is refused and the launch keeps what it had.
    """
    with _engines(base_lists_devices=True) as (hip, base):
        answer = engine_that_opens_no_card(hip)
    assert isinstance(answer, str), (
        "an engine that still lists a device under the switch was accepted: "
        "that launch opens every card it listed and reports nothing")
    assert "does not honour" in answer, answer
    assert "Vulkan0" in answer, (
        "the sentence must name the device that was still listed, so the log "
        f"says what was seen: {answer}")


def test_an_engine_that_cannot_be_asked_is_not_read_as_having_no_devices():
    """A refusal to answer is not an answer of "no devices"."""
    with _engines(base_refuses_to_answer=True) as (hip, base):
        answer = engine_that_opens_no_card(hip)
    assert isinstance(answer, str), answer
    assert "could not be re-enumerated" in answer, answer


def test_the_launch_keeps_its_rocm_filter_when_the_switch_is_not_honoured():
    cmd, env = _launch(gpu_layers=0, handed_engine="hip",
                       device="ROCm1", device_pci="0000:0e:00.0",
                       base_lists_devices=True)
    assert os.path.basename(cmd[0]) == "rocm-llama-server", cmd
    assert env is not None and env.get(ROCR_VISIBLE_DEVICES) == NO_CARD_AT_ALL, env
    assert VK_LOADER_DRIVERS_DISABLE not in env, env


def test_a_failure_inside_the_chooser_never_aborts_the_launch():
    """Choosing the engine for such a launch is an improvement to it, never a
    precondition for it. An unexpected failure while choosing must leave the
    instance starting as it would have, not stop it starting at all — found by
    the suite, where a test double raising from inside the chooser stopped
    start() before it built any command line.
    """
    real = serving_device.engine_that_opens_no_card

    def _raises(*_a, **_k):
        raise RuntimeError("a fault while choosing the engine")

    serving_device.engine_that_opens_no_card = _raises
    try:
        cmd, env = _launch(gpu_layers=0, handed_engine="hip",
                           device="ROCm1", device_pci="0000:0e:00.0")
    finally:
        serving_device.engine_that_opens_no_card = real
    assert cmd is not None, "the launch never built a command line"
    assert os.path.basename(cmd[0]) == "rocm-llama-server", cmd
    assert cmd[cmd.index("--device") + 1] == "none", cmd
    assert env is not None and env.get(ROCR_VISIBLE_DEVICES) == NO_CARD_AT_ALL, (
        "the filter the previous release applied must still be applied when "
        f"choosing fails: {env}")
