# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Multi-GPU serving-device pin: the serving model takes ONE assigned card.

Without a pin, llama.cpp's Vulkan backend splits layers across EVERY visible
device — on a dual-GPU box that silently occupies the card reserved for the
judge/eval co-resident instance. The pin threads one ggml device name (from
``llama-server --list-devices``) through start().

Selection policy (select_serving_device): the hardware detector's most-capable
DISCRETE card is ground truth (dedicated-VRAM size from sysfs); the
--list-devices entry whose reported heap matches it (within tolerance) is the
pin. An iGPU/APU heap is system-RAM-backed and never matches a discrete card's
dedicated VRAM, so integrated devices are excluded by construction — never by
name pattern. No discrete card / no match / enumeration failure = None = the
exact prior (no-pin) behavior.

Command-construction tests reuse the test_llama_device_none recorder pattern:
no server, model load, or device is ever touched.
"""
from __future__ import annotations

import contextlib
import socket
import tempfile

from intergen import llama_manager, serving_device
from intergen.llama_manager import LlamaManager
from intergen.serving_device import select_serving_device, select_serving_engine


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


def _build_cmd(gpu_layers: int, device: str | None) -> list[str]:
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
                    device=device,
                )
    finally:
        llama_manager.subprocess.Popen = real_popen
    assert _CmdRecorder.last_cmd is not None, (
        "start() never reached command construction — a pre-launch gate "
        "failed; the test environment is wrong, not the fix"
    )
    return _CmdRecorder.last_cmd


# ── start() argv contract ──────────────────────────────────────────────────────

def test_gpu_instance_with_device_pins_it():
    cmd = _build_cmd(gpu_layers=999, device="Vulkan1")
    assert "--device" in cmd, f"--device missing from pinned GPU argv: {cmd}"
    assert cmd[cmd.index("--device") + 1] == "Vulkan1"


def test_gpu_instance_without_device_has_no_device_flag():
    cmd = _build_cmd(gpu_layers=999, device=None)
    assert "--device" not in cmd, (
        f"no pin requested — argv must keep llama.cpp default devices: {cmd}"
    )


def test_cpu_instance_device_none_wins_over_pin():
    # A CPU-pinned instance must NEVER initialize an accelerator (F24), even if
    # a caller threads a device: --device none is supreme, exactly once.
    cmd = _build_cmd(gpu_layers=0, device="Vulkan1")
    assert cmd.count("--device") == 1, f"exactly one --device expected: {cmd}"
    assert cmd[cmd.index("--device") + 1] == "none"
    assert "Vulkan1" not in cmd


# ── select_serving_device policy (real-box fixtures) ─────────────────────────

# The dfb: two identical discrete cards + an APU whose system-RAM-backed heap
# REPORTS LARGER than either discrete card — the naive largest-heap pick fails
# here, which is why sysfs dedicated VRAM is the ground truth.
_LIST_DUAL_R9700_PLUS_APU = """\
Available devices:
  Vulkan0: AMD Radeon AI PRO R9700 (RADV GFX1201) (32624 MiB, 28352 MiB free)
  Vulkan1: AMD Radeon AI PRO R9700 (RADV GFX1201) (32624 MiB, 32566 MiB free)
  Vulkan2: AMD Ryzen 9 9950X 16-Core Processor (RADV RAPHAEL_MENDOCINO) (65201 MiB, 64905 MiB free)
"""

# The development-machine-shaped box: enumeration order puts the SMALLER card first — the
# serve pin must land on the larger card by VRAM, never on "the first device".
_LIST_SMALL_FIRST = """\
Available devices:
  Vulkan0: AMD Radeon RX 7600 (RADV NAVI33) (8176 MiB, 4751 MiB free)
  Vulkan1: AMD Radeon RX 7900 XT (RADV NAVI31) (20464 MiB, 20437 MiB free)
"""


def test_selector_identical_twins_first_match_leaves_other_free():
    dev = select_serving_device(list_output=_LIST_DUAL_R9700_PLUS_APU,
                                discrete_vram_mb=32768)
    assert dev == "Vulkan0"


def test_selector_never_picks_the_apu_despite_larger_heap():
    # 32 GB discrete ground truth vs a 65 GB APU heap: the APU must lose.
    dev = select_serving_device(list_output=_LIST_DUAL_R9700_PLUS_APU,
                                discrete_vram_mb=32768)
    assert dev != "Vulkan2"


def test_selector_picks_largest_card_not_first_device():
    dev = select_serving_device(list_output=_LIST_SMALL_FIRST,
                                discrete_vram_mb=20464)
    assert dev == "Vulkan1"


def test_selector_no_discrete_card_returns_none():
    assert select_serving_device(list_output=_LIST_SMALL_FIRST,
                                 discrete_vram_mb=0) is None


def test_selector_no_match_returns_none():
    # Ground truth names a card the enumeration doesn't show (hot-unplug,
    # driver skew): fail-safe to no pin, never a wrong pin.
    assert select_serving_device(list_output=_LIST_SMALL_FIRST,
                                 discrete_vram_mb=49152) is None


# ── display avoidance (the [PCI …] suffix the engine patch adds) ─────────────

# The REAL output of the patched engine's --list-devices on the dual-R9700
# workstation, captured 2026-08-04 (warnings included — the parser must read
# through preamble). Identical twins carry their PCI ids, and the enumeration
# puts the DISPLAY card (03:00.0, drives two DisplayPort monitors — note its
# lower free figure: it is painting the desktop) FIRST: on this real box,
# first-match picks the wrong card, which is exactly why avoidance exists.
_LIST_TWINS_WITH_PCI = """\
WARNING: radv is not a conformant Vulkan implementation, testing use only.
WARNING: radv is not a conformant Vulkan implementation, testing use only.
Available devices:
  Vulkan0: AMD Radeon AI PRO R9700 (RADV GFX1201) (32624 MiB, 29059 MiB free) [PCI 0000:03:00.0]
  Vulkan1: AMD Radeon AI PRO R9700 (RADV GFX1201) (32624 MiB, 32566 MiB free) [PCI 0000:07:00.0]
  Vulkan2: AMD Ryzen 9 9950X 16-Core Processor (RADV RAPHAEL_MENDOCINO) (65201 MiB, 64907 MiB free) [PCI 0000:13:00.0]
"""


def _fake_display_map(mapping):
    """A _pci_drives_display stand-in answering from a fixed pci→bool|None map."""
    return lambda pci, sysfs_root="/sys": mapping.get(pci)


def test_twins_with_ids_serving_avoids_the_display_card(monkeypatch):
    # 03:00.0 paints the desktop; 07:00.0 is headless. The pin must land on
    # the headless twin even though the display card enumerates first.
    monkeypatch.setattr(serving_device, "_pci_drives_display",
                        _fake_display_map({"0000:03:00.0": True,
                                           "0000:07:00.0": False,
                                           "0000:13:00.0": True}))
    dev = select_serving_device(list_output=_LIST_TWINS_WITH_PCI,
                                discrete_vram_mb=32768)
    assert dev == "Vulkan1"


def test_twins_with_ids_but_unknown_display_state_first_match_wins(monkeypatch):
    # sysfs unreadable everywhere → nothing is PROVABLY display-free → the
    # deterministic first match (the pre-suffix behavior), never a guess.
    monkeypatch.setattr(serving_device, "_pci_drives_display",
                        _fake_display_map({}))
    dev = select_serving_device(list_output=_LIST_TWINS_WITH_PCI,
                                discrete_vram_mb=32768)
    assert dev == "Vulkan0"


def test_old_shape_output_without_ids_keeps_first_match():
    # An unpatched engine build prints no suffix: selection must behave
    # exactly as before the patch existed.
    dev = select_serving_device(list_output=_LIST_DUAL_R9700_PLUS_APU,
                                discrete_vram_mb=32768)
    assert dev == "Vulkan0"


def test_suffix_never_rescues_a_vram_mismatch(monkeypatch):
    # The APU is display-free — but it failed the VRAM gate, and the display
    # preference ranks CANDIDATES only; it must never re-admit an excluded
    # device.
    monkeypatch.setattr(serving_device, "_pci_drives_display",
                        _fake_display_map({"0000:03:00.0": True,
                                           "0000:07:00.0": True,
                                           "0000:13:00.0": False}))
    dev = select_serving_device(list_output=_LIST_TWINS_WITH_PCI,
                                discrete_vram_mb=32768)
    assert dev != "Vulkan2"


# ── _pci_drives_display (kernel-records ground truth, fake sysfs) ────────────

def _mk_sysfs(tmp_path, pci_id, card, connectors):
    """Lay out <sysfs>/bus/pci/devices/<id>/drm/<card> + connector statuses."""
    (tmp_path / "bus" / "pci" / "devices" / pci_id / "drm" / card).mkdir(
        parents=True)
    for name, status in connectors.items():
        cdir = tmp_path / "class" / "drm" / f"{card}-{name}"
        cdir.mkdir(parents=True)
        (cdir / "status").write_text(status)


def test_pci_display_probe_connected(tmp_path):
    _mk_sysfs(tmp_path, "0000:03:00.0", "card1",
              {"DP-3": "connected", "DP-4": "disconnected"})
    assert serving_device._pci_drives_display(
        "0000:03:00.0", sysfs_root=str(tmp_path)) is True


def test_pci_display_probe_all_disconnected(tmp_path):
    _mk_sysfs(tmp_path, "0000:07:00.0", "card0",
              {"DP-1": "disconnected", "DP-2": "disconnected"})
    assert serving_device._pci_drives_display(
        "0000:07:00.0", sysfs_root=str(tmp_path)) is False


def test_pci_display_probe_headless_card_no_connectors(tmp_path):
    _mk_sysfs(tmp_path, "0000:0a:00.0", "card2", {})
    assert serving_device._pci_drives_display(
        "0000:0a:00.0", sysfs_root=str(tmp_path)) is False


def test_pci_display_probe_unknown_device_is_unknown(tmp_path):
    assert serving_device._pci_drives_display(
        "0000:ff:00.0", sysfs_root=str(tmp_path)) is None


# ── engine selection (the declared per-vendor preference table) ──────────────

def _fake_engines(tmp_path, monkeypatch, present):
    """Point ENGINE_SERVER_PATHS at tmp binaries; create only ``present``."""
    paths = {}
    for engine in ("cuda", "hip", "vulkan"):
        p = tmp_path / engine / "llama-server"
        paths[engine] = str(p)
        if engine in present:
            p.parent.mkdir(parents=True)
            p.write_text("#!/bin/sh\n")
            p.chmod(0o755)
    monkeypatch.setattr(serving_device, "ENGINE_SERVER_PATHS", paths)
    return paths


def test_engine_amd_prefers_hip_when_present(tmp_path, monkeypatch):
    # The residency-correctness preference: on AMD the HIP build places
    # weights in VRAM where RADV (GFX1201, measured 2026-08-03) left 24.4 GB
    # in GTT.
    paths = _fake_engines(tmp_path, monkeypatch, present={"hip", "vulkan"})
    assert select_serving_engine(vendor="amd") == ("hip", paths["hip"])


def test_engine_amd_without_hip_falls_to_vulkan(tmp_path, monkeypatch):
    paths = _fake_engines(tmp_path, monkeypatch, present={"vulkan"})
    assert select_serving_engine(vendor="amd") == ("vulkan", paths["vulkan"])


def test_engine_nvidia_provisional_order_is_vulkan_first(tmp_path, monkeypatch):
    # PROVISIONAL, pending the recorded decision walk: Vulkan measured FASTER
    # than CUDA on the reference NVIDIA card at this pin (2026-08-04,
    # docs/CUDA-ENGINE.md), so the table keeps it first even with the CUDA
    # build installed. When the walk rules otherwise, ENGINE_PREFERENCE and
    # this test change together.
    paths = _fake_engines(tmp_path, monkeypatch, present={"cuda", "vulkan"})
    assert select_serving_engine(vendor="nvidia") == ("vulkan", paths["vulkan"])


def test_engine_unknown_vendor_serves_vulkan(tmp_path, monkeypatch):
    paths = _fake_engines(tmp_path, monkeypatch, present={"cuda", "hip", "vulkan"})
    assert select_serving_engine(vendor="intel") == ("vulkan", paths["vulkan"])
    assert select_serving_engine(vendor="software") == ("vulkan", paths["vulkan"])


def test_engine_detection_unavailable_serves_vulkan(tmp_path, monkeypatch):
    # vendor=None asks the hardware detector; when detection itself fails the
    # default preference (the shipped Vulkan engine) is the fail-safe floor.
    import intergen.hardware as hw

    class _Boom:
        def __init__(self):
            raise RuntimeError("test sentinel: no detector")

    paths = _fake_engines(tmp_path, monkeypatch, present={"cuda", "hip", "vulkan"})
    monkeypatch.setattr(hw, "HardwareDetector", _Boom)
    assert select_serving_engine(vendor=None) == ("vulkan", paths["vulkan"])


def test_engine_config_pin_is_supreme_even_over_the_table(tmp_path, monkeypatch):
    paths = _fake_engines(tmp_path, monkeypatch, present={"cuda", "hip", "vulkan"})
    assert select_serving_engine(vendor="amd", engine_pin="cuda") == \
        ("cuda", paths["cuda"])


def test_engine_pin_of_absent_engine_still_stands(tmp_path, monkeypatch):
    # A pin is never silently substituted: the answer names the pinned engine
    # and its (absent) path; the launch refuses loudly on it.
    paths = _fake_engines(tmp_path, monkeypatch, present={"vulkan"})
    assert select_serving_engine(vendor="nvidia", engine_pin="cuda") == \
        ("cuda", paths["cuda"])


def test_engine_pin_of_unknown_name_yields_empty_path(tmp_path, monkeypatch):
    # A config typo is a loud boot failure (BINARY_ABSENT on the empty path),
    # never a quiet substitution.
    _fake_engines(tmp_path, monkeypatch, present={"vulkan"})
    engine, path = select_serving_engine(vendor="amd", engine_pin="cudda")
    assert engine == "cudda" and path == ""


# ── start() server_path contract (the enumerating binary launches) ───────────

def test_start_with_explicit_server_path_launches_exactly_it(tmp_path):
    server = tmp_path / "llama-server"
    server.write_text("#!/bin/sh\n")
    server.chmod(0o755)
    _CmdRecorder.last_cmd = None
    real_popen = llama_manager.subprocess.Popen
    llama_manager.subprocess.Popen = _CmdRecorder
    try:
        with tempfile.NamedTemporaryFile(suffix=".gguf") as model:
            mgr = LlamaManager()
            with contextlib.suppress(Exception):
                mgr.start(model.name, port=_free_port(), gpu_layers=999,
                          device="Vulkan1", server_path=str(server))
    finally:
        llama_manager.subprocess.Popen = real_popen
    assert _CmdRecorder.last_cmd is not None
    assert _CmdRecorder.last_cmd[0] == str(server)


def test_start_with_absent_server_path_refuses_loudly(tmp_path):
    # A resolved engine choice whose binary is gone must REFUSE — falling back
    # to a different engine's binary would launch a server whose device
    # namespace does not match the pin computed against this one.
    from intergen.llama_manager import StartFailure
    _CmdRecorder.last_cmd = None
    real_popen = llama_manager.subprocess.Popen
    llama_manager.subprocess.Popen = _CmdRecorder
    try:
        with tempfile.NamedTemporaryFile(suffix=".gguf") as model:
            mgr = LlamaManager()
            started = mgr.start(model.name, port=_free_port(), gpu_layers=999,
                                server_path=str(tmp_path / "gone"))
    finally:
        llama_manager.subprocess.Popen = real_popen
    assert started is False
    assert mgr.last_failure is StartFailure.BINARY_ABSENT
    assert _CmdRecorder.last_cmd is None, "launch must never be attempted"

