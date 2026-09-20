# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The pinned card is the only card the serving engine can see.

WHAT WAS MEASURED, on the two-card AMD machine on 2026-09-20. ``--device``
puts LAYERS on one card. It does not stop the ROCm runtime from opening every
other card: the runtime enumerates them all at startup, and an allocation the
layer pin does not cover — the vision projector — lands on whichever card the
runtime calls device 0. On that machine device 0 is the card driving the
desktop, so the projector sat on the display card while the offload plan's
arithmetic charged it to the pinned card. A plan that is arithmetically tidy
about a machine state that does not exist is worse than an obviously wrong
number, because nothing looks wrong.

THE FIX is the runtime's own filter: ROCR_VISIBLE_DEVICES removes cards below
everything else, and HIP_VISIBLE_DEVICES filters what is left. One visible
card leaves nowhere else for a default allocation to go.

THE WHOLE RISK IS RENUMBERING. Under the filter the pinned card becomes device
0. On the measured machine the 7900 XT is ROCm1 unfiltered and ROCm0 filtered,
so passing the unfiltered name under the filter names a device that does not
exist and the launch dies. Two different numbering schemes are involved — the
ROCr enumeration index the filter takes, and the ggml device name the engine
wants — and this code assumes no relationship between them: it derives a
candidate index from the kernel's KFD topology, RE-ENUMERATES with the filter
applied, and uses the filter only when exactly one device comes back carrying
the pinned card's PCI address. The ``--device`` name is then read from that
filtered listing.

WHAT THIS FILE PINS: the index derivation, including the integer ordering of
the kernel's node directories; every refusal path, because each one must leave
the launch exactly as it was before this existed; the renumbering, which is
the thing most likely to be "tidied" into passing the original name; and that
a launch with no verified filter passes no environment of its own at all.

Nothing here runs an engine, touches a card, or needs one to exist.
"""
from __future__ import annotations

import contextlib
import os
import socket
import tempfile

from intergen import llama_manager, serving_device
from intergen.llama_manager import LlamaManager
from intergen.serving_device import (
    HIP_VISIBLE_DEVICES,
    ROCR_VISIBLE_DEVICES,
    VisibilityFilter,
    rocr_index_by_pci,
    visibility_filter_for_pinned_card,
)

# The two-card machine the measurement comes from: the display card at bus 06
# is the kernel's first GPU node, the serving card at bus 0e the second.
DISPLAY_PCI = "0000:06:00.0"
SERVING_PCI = "0000:0e:00.0"

_FILTERED_ONE_CARD = """\
Available devices:
  ROCm0: AMD Radeon RX 7900 XT (20464 MiB, 13922 MiB free) [PCI 0000:0e:00.0]
"""

_UNFILTERED_TWO_CARDS = """\
Available devices:
  ROCm0: AMD Radeon RX 7600 (8176 MiB, 6842 MiB free) [PCI 0000:06:00.0]
  ROCm1: AMD Radeon RX 7900 XT (20464 MiB, 13922 MiB free) [PCI 0000:0e:00.0]
"""

_FILTERED_NO_PCI = """\
Available devices:
  ROCm0: AMD Radeon RX 7900 XT (20464 MiB, 13922 MiB free)
"""

_FILTERED_WRONG_CARD = """\
Available devices:
  ROCm0: AMD Radeon RX 7600 (8176 MiB, 6842 MiB free) [PCI 0000:06:00.0]
"""


def _topology(tmp: str, nodes: "list[tuple[str, int, int]]") -> str:
    """Write a fake KFD topology. Each node is (dir name, gfx version, location)."""
    root = os.path.join(tmp, "nodes")
    for name, gfx, location in nodes:
        d = os.path.join(root, name)
        os.makedirs(d)
        with open(os.path.join(d, "properties"), "w", encoding="utf-8") as fh:
            fh.write(f"gfx_target_version {gfx}\n"
                     f"location_id {location}\n"
                     "domain 0\n")
    return root


# location_id packs bus in bits 8..15: bus 0x06 is 1536, bus 0x0e is 3584.
_CPU_NODE = ("0", 0, 0)
_GPU_06 = ("1", 110002, 1536)
_GPU_0E = ("2", 110000, 3584)


# ── the index derivation ───────────────────────────────────────────────────

def test_the_index_skips_the_cpu_node_and_counts_gpus_in_order():
    with tempfile.TemporaryDirectory() as tmp:
        root = _topology(tmp, [_CPU_NODE, _GPU_06, _GPU_0E])
        assert rocr_index_by_pci(root) == {DISPLAY_PCI: 0, SERVING_PCI: 1}, (
            "the runtime numbers GPU agents in node order with the CPU node "
            "skipped; any other answer points the filter at the wrong card"
        )


def test_the_nodes_are_ordered_as_numbers_not_as_text():
    """A machine with ten or more nodes must not get node10 before node2."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _topology(tmp, [_CPU_NODE, _GPU_06, ("10", 110000, 3584)])
        got = rocr_index_by_pci(root)
        assert got == {DISPLAY_PCI: 0, SERVING_PCI: 1}, (
            f"node 10 sorted before node 1, so the indices name the wrong "
            f"cards: {got}"
        )


def test_an_unreadable_topology_is_empty_not_a_guess():
    assert rocr_index_by_pci("/nonexistent/kfd/topology/nodes") == {}


# ── the filter, and every way it refuses ───────────────────────────────────

def _filter(pci, list_output, nodes=(_CPU_NODE, _GPU_06, _GPU_0E)):
    with tempfile.TemporaryDirectory() as tmp:
        root = _topology(tmp, list(nodes))
        return visibility_filter_for_pinned_card(
            pci, "/opt/rocm/bin/llama-server",
            topology_root=root, list_output=list_output)


def test_a_verified_filter_names_the_card_and_the_filtered_device_name():
    got = _filter(SERVING_PCI, _FILTERED_ONE_CARD)
    assert isinstance(got, VisibilityFilter), got
    assert got.env == {ROCR_VISIBLE_DEVICES: "1", HIP_VISIBLE_DEVICES: "0"}, (
        f"the serving card is the kernel's second GPU node: {got.env}"
    )
    assert got.device == "ROCm0", (
        "the device name must come from the FILTERED listing — under the "
        "filter the pinned card is device 0, and passing its unfiltered name "
        f"would name a device that does not exist: {got.device}"
    )
    assert got.pci_id == SERVING_PCI


def test_the_renumbering_is_not_assumed_away():
    """Unfiltered the card is ROCm1; the launch must not be told ROCm1."""
    got = _filter(SERVING_PCI, _FILTERED_ONE_CARD)
    assert isinstance(got, VisibilityFilter)
    assert got.device != "ROCm1"


def test_a_filter_that_leaves_two_cards_visible_is_refused():
    got = _filter(SERVING_PCI, _UNFILTERED_TWO_CARDS)
    assert isinstance(got, str) and "not 1" in got, got


def test_a_filter_showing_a_different_card_is_refused():
    got = _filter(SERVING_PCI, _FILTERED_WRONG_CARD)
    assert isinstance(got, str) and DISPLAY_PCI in got, got


def test_a_listing_without_a_pci_address_is_refused():
    got = _filter(SERVING_PCI, _FILTERED_NO_PCI)
    assert isinstance(got, str) and "PCI" in got, got


def test_a_card_the_kernel_does_not_publish_is_refused():
    got = _filter("0000:99:00.0", _FILTERED_ONE_CARD)
    assert isinstance(got, str), got


def test_no_pci_address_is_refused():
    got = _filter(None, _FILTERED_ONE_CARD)
    assert isinstance(got, str), got


def test_an_unreadable_topology_is_refused():
    got = visibility_filter_for_pinned_card(
        SERVING_PCI, "/opt/rocm/bin/llama-server",
        topology_root="/nonexistent", list_output=_FILTERED_ONE_CARD)
    assert isinstance(got, str) and "KFD" in got, got


# ── the launch ─────────────────────────────────────────────────────────────

class _LaunchRecorder:
    """Popen stand-in: record the ENGINE launch and abort it.

    start() runs other programs on its way — the hardware detector shells out
    to lspci, and engine selection runs the server itself with
    ``--list-devices`` — so a recorder that grabs the first Popen records the
    wrong command and the test then passes or fails for a reason that has
    nothing to do with the launch. The launch is the command carrying
    ``--model``; everything else is handed to the real Popen and runs normally.
    """

    last_cmd: "list[str] | None" = None
    last_env: "dict[str, str] | None" = None
    real_popen = None

    def __new__(cls, cmd, **kwargs):
        argv = list(cmd)
        if "--model" not in argv:
            return cls.real_popen(argv, **kwargs)
        cls.last_cmd = argv
        cls.last_env = kwargs.get("env")
        raise RuntimeError("test sentinel: stop after cmd construction")


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@contextlib.contextmanager
def _fake_server():
    """An executable file standing in for the engine binary.

    start() is given this path explicitly, so engine selection never runs and
    the test does not depend on which llama-server builds the host carries.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "llama-server")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(path, 0o755)
        yield path


def _launch(device, device_pci, verdict):
    """Drive start() far enough to capture argv and the child's environment."""
    _LaunchRecorder.last_cmd = None
    _LaunchRecorder.last_env = None
    real_popen = llama_manager.subprocess.Popen
    real_filter = serving_device.visibility_filter_for_pinned_card
    _LaunchRecorder.real_popen = real_popen
    llama_manager.subprocess.Popen = _LaunchRecorder
    serving_device.visibility_filter_for_pinned_card = (
        lambda *a, **k: verdict)
    try:
        with tempfile.NamedTemporaryFile(suffix=".gguf") as model, \
                _fake_server() as server:
            mgr = LlamaManager()
            with contextlib.suppress(Exception):
                mgr.start(model.name, port=_free_port(), gpu_layers=999,
                          device=device, device_pci=device_pci,
                          server_path=server)
    finally:
        llama_manager.subprocess.Popen = real_popen
        serving_device.visibility_filter_for_pinned_card = real_filter
    assert _LaunchRecorder.last_cmd is not None, (
        "start() never reached command construction — a pre-launch gate "
        "failed; the test environment is wrong, not the fix"
    )
    return _LaunchRecorder.last_cmd, _LaunchRecorder.last_env


_VERIFIED = VisibilityFilter(
    env={ROCR_VISIBLE_DEVICES: "1", HIP_VISIBLE_DEVICES: "0"},
    device="ROCm0", pci_id=SERVING_PCI, reason="verified in the test")


def test_the_launch_carries_the_filter_in_the_childs_environment():
    cmd, env = _launch("ROCm1", SERVING_PCI, _VERIFIED)
    assert env is not None, (
        "the child was launched with the daemon's own environment, so every "
        "card stays visible and the projector can land on the display card"
    )
    assert env[ROCR_VISIBLE_DEVICES] == "1"
    assert env[HIP_VISIBLE_DEVICES] == "0"
    assert env.get("PATH") == os.environ.get("PATH"), (
        "the filter must ADD to the daemon's environment, not replace it"
    )


def test_the_launch_passes_the_filtered_device_name():
    cmd, _env = _launch("ROCm1", SERVING_PCI, _VERIFIED)
    assert cmd[cmd.index("--device") + 1] == "ROCm0", (
        f"--device must follow the filtered listing, not the unfiltered "
        f"name: {cmd}"
    )


def test_a_refused_filter_leaves_the_launch_exactly_as_it_was():
    cmd, env = _launch("ROCm1", SERVING_PCI, "no card could be named")
    assert env is None, (
        "with no verified filter the child must inherit the daemon's "
        "environment exactly as before, not a rebuilt copy"
    )
    assert cmd[cmd.index("--device") + 1] == "ROCm1"


def test_a_non_rocm_engine_is_not_filtered():
    """ROCR_VISIBLE_DEVICES means nothing to the Vulkan or CUDA builds."""
    cmd, env = _launch("Vulkan1", SERVING_PCI, _VERIFIED)
    assert env is None, "a Vulkan pin must not be given a ROCm filter"
    assert cmd[cmd.index("--device") + 1] == "Vulkan1"


def test_a_cpu_pinned_instance_is_untouched():
    real_popen = llama_manager.subprocess.Popen
    _LaunchRecorder.real_popen = real_popen
    _LaunchRecorder.last_cmd = None
    _LaunchRecorder.last_env = None
    llama_manager.subprocess.Popen = _LaunchRecorder
    try:
        with tempfile.NamedTemporaryFile(suffix=".gguf") as model:
            mgr = LlamaManager()
            with contextlib.suppress(Exception), _fake_server() as server:
                mgr.start(model.name, port=_free_port(), gpu_layers=0,
                          embedding=True, device="ROCm1",
                          device_pci=SERVING_PCI, server_path=server)
    finally:
        llama_manager.subprocess.Popen = real_popen
    cmd = _LaunchRecorder.last_cmd
    assert cmd[cmd.index("--device") + 1] == "none", cmd
    assert _LaunchRecorder.last_env is None, (
        "a CPU instance opens no card at all and must be given no filter"
    )
