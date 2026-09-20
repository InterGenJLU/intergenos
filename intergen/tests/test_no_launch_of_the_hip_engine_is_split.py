# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""No launch of the ROCm engine is ever split across cards.

Two launches carried no device-visibility filter and were measured opening
every card on a two-card AMD machine on 2026-09-20:

* a GPU launch for which no card could be pinned — the engine then splits the
  model across every card it can see, and a context-checkpoint restore under a
  split model faults the GPU and aborts the engine (reproduced three times out
  of three on that machine; absent four times out of four with a single card
  visible);
* a launch that takes no card at all — the instance that serves embeddings on
  the CPU still initialised the graphics runtime across both cards and held a
  small allocation on each.

These tests pin both answers, and pin the cases that must stay exactly as they
were: another backend's engine, and a launch that already has a pinned card.
"""
import contextlib
import os
import socket
import tempfile

from intergen import llama_manager, serving_device
from intergen.llama_manager import LlamaManager
from intergen.serving_device import (
    HIP_VISIBLE_DEVICES, NO_CARD_AT_ALL, ROCR_VISIBLE_DEVICES,
    VisibilityFilter, largest_display_free_card,
    visibility_filter_for_an_unpinned_launch)

SMALL_CARD = "0000:06:00.0"     # 8 GiB, drives the desktop on the real machine
BIG_CARD = "0000:0e:00.0"       # 20 GiB, display-free
THIRD_CARD = "0000:10:00.0"

TWO_CARDS = (
    "Available devices:\n"
    f"  ROCm0: AMD Radeon RX 7600 (8176 MiB, 8116 MiB free) [PCI {SMALL_CARD}]\n"
    f"  ROCm1: AMD Radeon RX 7900 XT (20464 MiB, 12796 MiB free) [PCI {BIG_CARD}]\n")
ONE_CARD = (
    "Available devices:\n"
    f"  ROCm0: AMD Radeon RX 7900 XT (20464 MiB, 20280 MiB free) [PCI {BIG_CARD}]\n")
TWIN_CARDS = (
    "Available devices:\n"
    f"  ROCm0: AMD Radeon RX 7900 XT (20464 MiB, 20280 MiB free) [PCI {BIG_CARD}]\n"
    f"  ROCm1: AMD Radeon RX 7900 XT (20464 MiB, 20280 MiB free) [PCI {THIRD_CARD}]\n")


def _topology(tmp, cards):
    """A KFD topology directory the real reader can walk: one CPU node that
    publishes no architecture, then one GPU node per card in order."""
    root = os.path.join(tmp, "nodes")
    os.makedirs(os.path.join(root, "node0"))
    with open(os.path.join(root, "node0", "properties"), "w") as fh:
        fh.write("gfx_target_version 0\ncpu_cores_count 16\n")
    for i, pci in enumerate(cards, start=1):
        node = os.path.join(root, f"node{i}")
        os.makedirs(node)
        domain, bus, rest = pci.split(":")
        dev, func = rest.split(".")
        location = (int(bus, 16) << 8) | (int(dev, 16) << 3) | int(func)
        with open(os.path.join(node, "properties"), "w") as fh:
            fh.write(f"gfx_target_version 110000\ndomain {int(domain, 16)}\n"
                     f"location_id {location}\n")
    return root


def _sysfs(tmp, connected, unreadable=()):
    """A sysfs tree the display reader can walk. ``connected`` names the cards
    driving a display; ``unreadable`` names cards whose connector status cannot
    be read, which the reader must treat as unknown, never as display-free."""
    root = os.path.join(tmp, "sys")
    for index, pci in enumerate((SMALL_CARD, BIG_CARD, THIRD_CARD)):
        card = f"card{index}"
        os.makedirs(os.path.join(root, "bus", "pci", "devices", pci, "drm", card))
        conn = os.path.join(root, "class", "drm", f"{card}-DP-1")
        os.makedirs(conn)
        status = os.path.join(conn, "status")
        if pci in unreadable:
            os.makedirs(status)          # a directory cannot be read as a file
        else:
            with open(status, "w") as fh:
                fh.write("connected\n" if pci in connected else "disconnected\n")
    return root


# ── choosing the card ──────────────────────────────────────────────────────

def test_the_largest_display_free_card_is_chosen():
    with tempfile.TemporaryDirectory() as tmp:
        chosen = largest_display_free_card(
            list_output=TWO_CARDS, sysfs_root=_sysfs(tmp, {SMALL_CARD}),
            topology_root=_topology(tmp, [SMALL_CARD, BIG_CARD]))
    assert chosen == ("ROCm1", BIG_CARD, 20464), chosen


def test_a_card_driving_a_display_is_not_chosen_even_when_it_is_the_only_one_left():
    with tempfile.TemporaryDirectory() as tmp:
        chosen = largest_display_free_card(
            list_output=TWO_CARDS,
            sysfs_root=_sysfs(tmp, {SMALL_CARD, BIG_CARD}),
            topology_root=_topology(tmp, [SMALL_CARD, BIG_CARD]))
    assert isinstance(chosen, str) and "driving a display" in chosen, chosen


def test_a_card_whose_display_state_cannot_be_read_is_not_chosen():
    with tempfile.TemporaryDirectory() as tmp:
        chosen = largest_display_free_card(
            list_output=TWO_CARDS,
            sysfs_root=_sysfs(tmp, {SMALL_CARD}, unreadable={BIG_CARD}),
            topology_root=_topology(tmp, [SMALL_CARD, BIG_CARD]))
    assert isinstance(chosen, str), chosen


def test_one_card_is_not_a_split_to_prevent():
    with tempfile.TemporaryDirectory() as tmp:
        chosen = largest_display_free_card(
            list_output=ONE_CARD, sysfs_root=_sysfs(tmp, set()),
            topology_root=_topology(tmp, [BIG_CARD]))
    assert isinstance(chosen, str) and "no split to prevent" in chosen, chosen


def test_two_identical_cards_choose_the_same_one_every_time():
    with tempfile.TemporaryDirectory() as tmp:
        sysfs = _sysfs(tmp, set())
        topo = _topology(tmp, [BIG_CARD, THIRD_CARD])
        first = largest_display_free_card(list_output=TWIN_CARDS,
                                          sysfs_root=sysfs, topology_root=topo)
        reversed_listing = "\n".join(
            [TWIN_CARDS.splitlines()[0]] + TWIN_CARDS.splitlines()[:0:-1]) + "\n"
        second = largest_display_free_card(list_output=reversed_listing,
                                           sysfs_root=sysfs, topology_root=topo)
    assert first[1] == second[1] == BIG_CARD, (first, second)


# ── verifying the filter for a launch with no pin ──────────────────────────

def test_an_unpinned_launch_gets_a_verified_filter_for_the_chosen_card():
    with tempfile.TemporaryDirectory() as tmp:
        verdict = visibility_filter_for_an_unpinned_launch(
            "/nonexistent/llama-server",
            sysfs_root=_sysfs(tmp, {SMALL_CARD}),
            topology_root=_topology(tmp, [SMALL_CARD, BIG_CARD]),
            list_output=TWO_CARDS,
            filtered_list_output=(
                "Available devices:\n"
                f"  ROCm0: AMD Radeon RX 7900 XT (20464 MiB, 20280 MiB free) "
                f"[PCI {BIG_CARD}]\n"))
    assert isinstance(verdict, VisibilityFilter), verdict
    assert verdict.pci_id == BIG_CARD
    assert verdict.device == "ROCm0"
    assert verdict.env == {ROCR_VISIBLE_DEVICES: "1", HIP_VISIBLE_DEVICES: "0"}
    assert "no card was pinned" in verdict.reason


def test_an_unpinned_filter_showing_the_wrong_card_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        verdict = visibility_filter_for_an_unpinned_launch(
            "/nonexistent/llama-server",
            sysfs_root=_sysfs(tmp, {SMALL_CARD}),
            topology_root=_topology(tmp, [SMALL_CARD, BIG_CARD]),
            list_output=TWO_CARDS,
            filtered_list_output=(
                "Available devices:\n"
                f"  ROCm0: AMD Radeon RX 7600 (8176 MiB, 8116 MiB free) "
                f"[PCI {SMALL_CARD}]\n"))
    assert isinstance(verdict, str), verdict
    assert BIG_CARD in verdict and SMALL_CARD in verdict


# ── the launch itself ──────────────────────────────────────────────────────

class _LaunchRecorder:
    """Popen stand-in that records the ENGINE launch and aborts it.

    start() runs other programs on its way, so the recorder selects the launch
    by the command carrying ``--model`` and hands everything else to the real
    Popen.
    """

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
def _fake_server(as_the_hip_engine, base_engine_installed=False):
    """An executable standing in for the engine binary.

    When ``as_the_hip_engine`` is true the recipe-defined HIP path is pointed
    at it for the duration, because the launch decides whether this backend can
    be filtered from the BINARY, not from a device name — an unpinned launch
    has no device name to read.

    The base engine's path is ALWAYS pointed somewhere inside the temporary
    directory, and by default at a file that does not exist. Without that these
    tests read whichever engines the machine running them happens to have
    installed, and a launch that takes no card now asks whether a base engine
    is present — so the result would differ between two machines and say
    nothing about the code.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "llama-server")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(path, 0o755)
        base = os.path.join(tmp, "base-llama-server")
        if base_engine_installed:
            with open(base, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\nexit 0\n")
            os.chmod(base, 0o755)
        saved = serving_device.ENGINE_SERVER_PATHS["hip"]
        saved_base = serving_device.ENGINE_SERVER_PATHS["vulkan"]
        serving_device.ENGINE_SERVER_PATHS["vulkan"] = base
        if as_the_hip_engine:
            serving_device.ENGINE_SERVER_PATHS["hip"] = path
        try:
            yield path
        finally:
            serving_device.ENGINE_SERVER_PATHS["hip"] = saved
            serving_device.ENGINE_SERVER_PATHS["vulkan"] = saved_base


def _launch(*, gpu_layers, device, device_pci, hip_engine,
            unpinned_verdict=None, base_engine_installed=False):
    _LaunchRecorder.last_cmd = None
    _LaunchRecorder.last_env = None
    real_popen = llama_manager.subprocess.Popen
    real_unpinned = serving_device.visibility_filter_for_an_unpinned_launch
    _LaunchRecorder.real_popen = real_popen
    llama_manager.subprocess.Popen = _LaunchRecorder
    if unpinned_verdict is not None:
        serving_device.visibility_filter_for_an_unpinned_launch = (
            lambda *a, **k: unpinned_verdict)
    try:
        with tempfile.NamedTemporaryFile(suffix=".gguf") as model, \
                _fake_server(hip_engine, base_engine_installed) as server:
            mgr = LlamaManager()
            with contextlib.suppress(Exception):
                mgr.start(model.name, port=_free_port(), gpu_layers=gpu_layers,
                          device=device, device_pci=device_pci,
                          embedding=(gpu_layers == 0), server_path=server)
    finally:
        llama_manager.subprocess.Popen = real_popen
        serving_device.visibility_filter_for_an_unpinned_launch = real_unpinned
    assert _LaunchRecorder.last_cmd is not None, (
        "start() never reached command construction — a pre-launch gate "
        "failed; the test environment is wrong, not the fix")
    return _LaunchRecorder.last_cmd, _LaunchRecorder.last_env


_CHOSEN = VisibilityFilter(
    env={ROCR_VISIBLE_DEVICES: "1", HIP_VISIBLE_DEVICES: "0"},
    device="ROCm0", pci_id=BIG_CARD, reason="chosen in the test")


def test_a_gpu_launch_with_no_pin_is_filtered_to_the_chosen_card():
    cmd, env = _launch(gpu_layers=999, device=None, device_pci=None,
                       hip_engine=True, unpinned_verdict=_CHOSEN)
    assert env is not None, (
        "an unpinned launch was handed the daemon's whole environment, so the "
        "engine sees every card and splits the model across them")
    assert env[ROCR_VISIBLE_DEVICES] == "1"
    assert env[HIP_VISIBLE_DEVICES] == "0"
    assert cmd[cmd.index("--device") + 1] == "ROCm0", cmd


def test_an_unpinned_launch_that_cannot_be_verified_is_left_alone():
    cmd, env = _launch(gpu_layers=999, device=None, device_pci=None,
                       hip_engine=True,
                       unpinned_verdict="no card could be chosen")
    assert env is None, (
        "with no verified filter the child must inherit the daemon's "
        "environment exactly as before")
    assert "--device" not in cmd, cmd


def test_an_unpinned_launch_of_another_backend_is_untouched():
    cmd, env = _launch(gpu_layers=999, device=None, device_pci=None,
                       hip_engine=False, unpinned_verdict=_CHOSEN)
    assert env is None, (
        "ROCR_VISIBLE_DEVICES means nothing to the Vulkan or CUDA builds and "
        "must not be set for them")
    assert "--device" not in cmd, cmd


def test_a_cpu_served_instance_on_a_machine_with_only_this_engine_still_hides_the_cards():
    """With no other engine build installed, the filter this file added still
    applies. It does not stop the ROCm runtime opening the cards — measured on
    2026-09-20, the instance still held 28 KiB of video memory and 2088 KiB of
    system memory on each — but it is what such a machine can do, and
    test_a_processor_served_instance_holds_nothing_on_any_card pins what a
    machine that also carries the base engine does instead.
    """
    cmd, env = _launch(gpu_layers=0, device="ROCm1", device_pci=BIG_CARD,
                       hip_engine=True, base_engine_installed=False)
    assert env is not None, (
        "the CPU-served instance still inherits every card: it initialises "
        "the graphics runtime on each one and holds an allocation there")
    assert env[ROCR_VISIBLE_DEVICES] == NO_CARD_AT_ALL, env[ROCR_VISIBLE_DEVICES]
    assert HIP_VISIBLE_DEVICES not in env or env[HIP_VISIBLE_DEVICES] != "0", (
        "a launch that opens no card must not also name a device index")
    assert cmd[cmd.index("--device") + 1] == "none", cmd
    assert env.get("PATH") == os.environ.get("PATH"), (
        "the filter must ADD to the daemon's environment, not replace it")


def test_a_cpu_served_instance_of_another_backend_is_never_given_this_filter():
    """ROCR_VISIBLE_DEVICES means nothing to a build that is not the ROCm one
    and must never be set for it. Such a launch does get the graphics-loader
    switch, which is a different variable and a different file's subject.
    """
    cmd, env = _launch(gpu_layers=0, device="ROCm1", device_pci=BIG_CARD,
                       hip_engine=False, base_engine_installed=False)
    assert env is None or ROCR_VISIBLE_DEVICES not in env, env
    assert cmd[cmd.index("--device") + 1] == "none", cmd
