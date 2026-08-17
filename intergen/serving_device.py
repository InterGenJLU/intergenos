# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Serving-engine and serving-device selection.

Two decisions are made here, in order, and both are overridable from config:

1. WHICH ENGINE serves (:func:`select_serving_engine`): the box may carry up
   to three llama-server builds — the shipped Vulkan default, the HIP variant
   and the CUDA variant, each at its own fixed path. The choice is a declared
   per-vendor preference table over engines that are actually present; an
   explicit ``llama_server.engine`` config value wins over the table.

2. WHICH DEVICE the serving model pins to (:func:`select_serving_device`):
   on a multi-GPU box the serving model takes ONE card and leaves the others
   free (an eval/judge instance co-resident on the second card is the case
   this was built for). Selection matches the hardware detector's discrete
   card against the chosen engine's own ``--list-devices`` output, and when
   more than one entry matches — identical twin cards — it prefers a card
   that is NOT driving any display, resolved through the PCI id each device
   line carries. Returns ``None`` for no pin, which is exactly llama.cpp's
   own default behaviour, so a box where selection is unavailable is
   unchanged.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess

# The three engine builds and where each installs its server binary. These are
# recipe-defined paths, not search heuristics: the Vulkan default engine
# (packages/ai/llama-cpp) owns /usr/bin/llama-server; the HIP variant
# (packages/compute/llama-cpp-hip) installs under the ROCm prefix; the CUDA
# variant (packages/compute/llama-cpp-cuda) is static under its own prefix.
ENGINE_SERVER_PATHS: dict[str, str] = {
    "cuda":   "/opt/llama-cpp-cuda/bin/llama-server",
    "hip":    "/opt/rocm/bin/llama-server",
    "vulkan": "/usr/bin/llama-server",
}

# The HIP build is compiled for a DECLARED list of AMD GPU architectures, and it
# contains device code for those and no others. The list is written once in
# packages/compute/llama-cpp-hip/package.yml (`gpu_targets`), the recipe passes
# it to cmake as -DGPU_TARGETS, and the recipe also installs it here so the
# runtime can read the same list rather than carry a second copy that drifts.
HIP_GPU_TARGETS_PATH = "/opt/rocm/share/llama-cpp-hip/gpu-targets"

# Where the amdgpu kernel driver publishes each compute node's architecture.
KFD_TOPOLOGY_NODES = "/sys/class/kfd/kfd/topology/nodes"

# The DECLARED per-vendor engine preference, tried in order over engines whose
# server binary is present. One table, visible here, so a preference change is
# one line — never scattered conditionals.
#
#   amd: HIP before Vulkan on residency-correctness grounds — measured
#   2026-08-03 on the dual-R9700 box: under RADV (GFX1201 non-conformant) the
#   served model's weights sat in GTT (24.4 GB) instead of VRAM (0.1 GB). The
#   HIP build places them in VRAM. A HIP-vs-Vulkan speed measurement on that
#   box is owed and will be recorded here when taken.
#
#   nvidia: PROVISIONAL — Vulkan first, pending a decision walk. Measured
#   2026-08-04 on a GeForce RTX 3070 Ti Laptop (cc 8.6, driver 580.159.04,
#   9B Q4_K_M, same source pin both engines, condition-proven, two orderings
#   agreeing): the Vulkan engine was FASTER than the CUDA engine on every
#   metric taken (pp512 −3.1%, pp2048 −4.2%, tg128 −6.5%; the driver reports
#   NV_coopmat2, so the Vulkan path also reaches the tensor cores). Method and
#   numbers: docs/CUDA-ENGINE.md. Keeping the measured-faster engine first
#   preserves shipping behaviour; the CUDA engine stays available and one
#   config line ("llama_server.engine": "cuda") or one edit here flips it.
#   This entry must not be reordered without a recorded decision.
ENGINE_PREFERENCE: dict[str, list[str]] = {
    "amd":    ["hip", "vulkan"],
    "nvidia": ["vulkan", "cuda"],
}
_DEFAULT_PREFERENCE: list[str] = ["vulkan"]

# Matches ggml's --list-devices lines, in both shapes:
#   "  Vulkan0: NAME (TOTAL MiB, FREE MiB free)"
#   "  Vulkan0: NAME (TOTAL MiB, FREE MiB free) [PCI 0000:03:00.0]"
# The bracketed suffix is added by the in-tree list-devices-pci-id.patch every
# engine recipe applies (the id is ggml_backend_dev_props.device_id,
# "domain:bus:device.function", printed only when the backend carries one), so
# the tail is OPTIONAL by design — unpatched builds and id-less devices still
# parse. intergen.hardware._LIST_DEVICES_RE is the same pattern minus the name
# group; the two must change in lockstep.
_DEVICE_LINE_RE = re.compile(
    r"^\s+(?P<name>\w+?\d+):\s+(?P<desc>.+?)\s+\((?P<total>\d+)\s*MiB,"
    r"\s*\d+\s*MiB free\)"
    r"(?:\s+\[PCI\s+(?P<pci>[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}"
    r"\.[0-7])\])?\s*$", re.MULTILINE)

# A --list-devices total is accepted as "this is the discrete card" when it is
# within this fraction of the sysfs dedicated-VRAM size. ggml reports the Vulkan
# heap, sysfs reports the PCI BAR/VRAM region — they differ by carve-outs of a
# few hundred MiB, never by the >2x gap that separates a discrete card from an
# iGPU/APU's system-RAM-backed heap (the failure this check exists to exclude:
# an APU can REPORT a bigger heap than a 32 GB discrete card).
_DEVICE_VRAM_TOLERANCE = 0.10


def _gfx_name(target_version: int) -> str | None:
    """Turn a KFD ``gfx_target_version`` integer into its gfx name.

    The kernel encodes the architecture as ``major*10000 + minor*100 + step``,
    and the conventional name spells the minor and step as single hex digits:
    90012 is gfx90c, 110000 is gfx1100, 120001 is gfx1201. Returns None for a
    value that cannot be an architecture, so an unreadable or zero property is
    never turned into a confident-looking answer.
    """
    if not isinstance(target_version, int) or target_version <= 0:
        return None
    major, rest = divmod(target_version, 10000)
    minor, step = divmod(rest, 100)
    if major <= 0 or minor > 15 or step > 15:
        return None
    return f"gfx{major}{minor:x}{step:x}"


def detect_amd_gfx_targets(topology_root: str = KFD_TOPOLOGY_NODES) -> set[str]:
    """The gfx architecture of every AMD compute node the kernel reports.

    Read from the amdgpu driver's own topology rather than from a tool, so no
    ROCm userspace has to be installed for the answer to be available — which
    matters, because this is used to decide whether installing that userspace
    would be useful at all.

    An empty set means "nothing was readable", which callers must treat as
    unknown rather than as "unsupported".
    """
    found: set[str] = set()
    try:
        nodes = sorted(os.listdir(topology_root))
    except OSError:
        return found
    for node in nodes:
        props = os.path.join(topology_root, node, "properties")
        try:
            with open(props, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        for line in text.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] == "gfx_target_version":
                try:
                    name = _gfx_name(int(parts[1]))
                except ValueError:
                    name = None
                if name:
                    found.add(name)
    return found


def hip_build_gpu_targets(path: str = HIP_GPU_TARGETS_PATH) -> set[str]:
    """The architectures the installed HIP build actually carries code for.

    Read from the file the HIP recipe installs. An empty set means the file is
    absent or unreadable, which callers treat as unknown.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError:
        return set()
    targets = set()
    for chunk in raw.replace(";", " ").replace(",", " ").split():
        chunk = chunk.strip()
        if chunk and not chunk.startswith("#"):
            targets.add(chunk)
    return targets


def hip_is_supported_here(topology_root: str = KFD_TOPOLOGY_NODES,
                          targets_path: str = HIP_GPU_TARGETS_PATH
                          ) -> bool | None:
    """Whether the installed HIP build has device code for this machine's GPU.

    Returns True when at least one detected architecture appears in the build's
    target list, False when architectures were detected and NONE of them do, and
    None when either side is unknown.

    WHY THIS GATE EXISTS. Being an AMD part is not the same as being a part this
    build can run on. The shipped HIP build declares
    ``gfx1100;gfx1102;gfx1201``; an APU reporting gfx90c is an AMD GPU with no
    device code in that build, and llama-server SEGFAULTS at model load rather
    than reporting a clean refusal. Selecting HIP by vendor alone therefore
    turns a working Vulkan installation into a crash, which is why the answer
    has to come from the architecture and not from the vendor string.

    The three-valued return is deliberate. "I could not tell" and "I checked and
    it will not work" have different correct responses, and collapsing them
    would either block HIP on every machine whose topology is unreadable or
    claim support on machines that have none.
    """
    detected = detect_amd_gfx_targets(topology_root)
    if not detected:
        return None
    supported = hip_build_gpu_targets(targets_path)
    if not supported:
        return None
    return bool(detected & supported)


def select_serving_engine(vendor: str | None = None,
                          engine_pin: str | None = None) -> tuple[str, str]:
    """Choose the engine that serves, and the server binary it runs.

    Returns ``(engine, server_path)``. An explicit ``engine_pin`` (the
    ``llama_server.engine`` config value) is supreme — the same user-control
    contract as ``gpu_layers``: honoured verbatim when its binary is present,
    and when the pinned binary is ABSENT the pin still stands as the answer
    (the caller's launch then fails loudly on the missing binary) — a pin is
    never silently substituted. With no pin, the vendor's row in
    :data:`ENGINE_PREFERENCE` is tried in order over engines whose server
    binary exists; the fallback in every case is the shipped Vulkan default.

    ``vendor`` is the hardware detector's GPU vendor string ("amd", "nvidia",
    "intel", "software", …); ``None`` means detect it here.

    An UNKNOWN pinned engine name yields an empty path: the launch then
    refuses loudly (BINARY_ABSENT, naming the empty path) instead of silently
    serving a different engine than the config states — a config typo is a
    loud boot failure, never a quiet substitution.
    """
    if engine_pin and engine_pin not in ("auto", ""):
        pin = engine_pin.strip().lower()
        return pin, ENGINE_SERVER_PATHS.get(pin, "")

    if vendor is None:
        try:
            from intergen.hardware import HardwareDetector
            vendor, _model, _vram_mb = HardwareDetector()._detect_gpu()
        except Exception:
            vendor = None

    for engine in ENGINE_PREFERENCE.get(vendor or "", _DEFAULT_PREFERENCE):
        path = ENGINE_SERVER_PATHS[engine]
        if not (os.path.isfile(path) and os.access(path, os.X_OK)):
            continue
        # Present is not the same as usable. The HIP build carries device code
        # only for the architectures it was compiled for, and on an AMD GPU
        # outside that list llama-server segfaults at model load — so a machine
        # that would have served fine on Vulkan crashes instead. Only a
        # MEASURED "no" skips the engine; an unreadable topology or a missing
        # target list leaves the preference alone, because refusing on "I could
        # not tell" would strand every machine whose driver state is unusual.
        if engine == "hip" and hip_is_supported_here() is False:
            continue
        return engine, path
    return "vulkan", ENGINE_SERVER_PATHS["vulkan"]


def engine_ladder(vendor: str | None = None) -> list[tuple[str, str]]:
    """The engines this machine could serve with, preferred first.

    Returns ``[(engine, server_path), ...]`` over engines whose binary is
    present and executable, in the vendor's declared preference order, with the
    shipped Vulkan engine appended as the floor when it is present and not
    already listed.

    This is the same walk :func:`select_serving_engine` does, exposed as a list
    so a caller that has just watched an engine FAIL can move to the next one
    instead of relaunching the one that died. An engine that is present is not
    an engine that works — a HIP build can segfault at model load on hardware
    outside its architecture list — and without somewhere to fall back to, the
    only outcome is the restart budget draining and the assistant going silent
    on a machine that had a working engine available the whole time.

    The architecture gate is applied here too, so a HIP build that measurably
    cannot run on this GPU is not offered as a rung.
    """
    ladder: list[tuple[str, str]] = []
    order = list(ENGINE_PREFERENCE.get(vendor or "", _DEFAULT_PREFERENCE))
    for engine in order + ["vulkan"]:
        if any(e == engine for e, _ in ladder):
            continue
        path = ENGINE_SERVER_PATHS.get(engine, "")
        if not path or not (os.path.isfile(path) and os.access(path, os.X_OK)):
            continue
        if engine == "hip" and hip_is_supported_here() is False:
            continue
        ladder.append((engine, path))
    return ladder


def next_engine_after(failed_engine: str | None,
                      vendor: str | None = None) -> tuple[str, str] | None:
    """The next rung below ``failed_engine``, or None when there is none.

    ``None``/unknown for ``failed_engine`` means "start at the top". Returning
    None is the honest end of the ladder: every engine this machine has has now
    been tried, and the caller must fail loudly rather than loop.
    """
    ladder = engine_ladder(vendor)
    if not ladder:
        return None
    if not failed_engine:
        return ladder[0]
    for i, (engine, _path) in enumerate(ladder):
        if engine == failed_engine:
            return ladder[i + 1] if i + 1 < len(ladder) else None
    # The failed engine is not on the ladder at all (an explicit pin, or a
    # build that has since been removed). Offer the top rung, which is the
    # closest thing to "try something else" that is still true.
    return ladder[0]


def _pci_drives_display(pci_id: str, sysfs_root: str = "/sys") -> bool | None:
    """Whether the GPU at ``pci_id`` is driving a connected display.

    Resolution is through the kernel's own records, no tools:
    ``<sysfs>/bus/pci/devices/<id>/drm/`` names the card's DRM node(s), and
    each connector's ``<sysfs>/class/drm/<card>-*/status`` says whether a
    display is attached. Returns ``True`` when any connector on the card
    reports "connected", ``False`` when the card exists and none do, and
    ``None`` when the mapping cannot be read (no DRM node, no such PCI
    device) — the caller treats ``None`` as "unknown", never as an answer.
    """
    drm_dir = os.path.join(sysfs_root, "bus", "pci", "devices", pci_id, "drm")
    try:
        cards = [c for c in os.listdir(drm_dir) if re.fullmatch(r"card\d+", c)]
    except OSError:
        return None
    if not cards:
        return None
    any_unreadable = False
    for card in cards:
        for status_path in glob.glob(
                os.path.join(sysfs_root, "class", "drm", f"{card}-*", "status")):
            try:
                with open(status_path, encoding="utf-8") as fh:
                    if fh.read().strip() == "connected":
                        return True
            except OSError:
                any_unreadable = True
    # A card with no connectors at all (a headless compute card) is honestly
    # "not driving a display". A card with an UNREADABLE connector status is
    # NOT — claiming display-free on a failed read could pin serving onto the
    # very card painting the desktop, so unreadable = unknown.
    if any_unreadable:
        return None
    return False


def select_serving_device(list_output: str | None = None,
                          discrete_vram_mb: int | None = None,
                          server: str | None = None) -> str | None:
    """Pick the ggml device the SERVING model should pin on a multi-GPU box.

    Policy: the hardware detector's most-capable DISCRETE card serves (its
    dedicated-VRAM size is the ground truth); the --list-devices entries whose
    reported total matches that size (within tolerance) are the candidates.
    iGPU/APU entries never match a discrete card's dedicated VRAM (their heap
    is system-RAM-backed), so they are excluded by construction, not by name
    pattern. Among the candidates — identical twins, e.g. dual R9700, where
    description-matching CANNOT distinguish the cards — the one whose PCI id
    (the bracketed suffix the in-tree engine patch adds) maps to a DRM card
    with NO connected display is preferred, so the serving model stays off the
    card that is painting the desktop and the display card stays free for the
    judge/eval instance. When no candidate is provably display-free (no PCI
    ids in the output, or sysfs unreadable), the first match wins exactly as
    before — the suffix is an upgrade, never a requirement.

    ``server`` names the llama-server binary to enumerate with, and the
    caller passes the ENGINE'S OWN binary (from :func:`select_serving_engine`)
    — device names are backend-local ("CUDA0" is not "Vulkan0"), so the
    binary that enumerates must be the binary that launches.

    Returns None (no pin — llama.cpp default behavior) when: no discrete card,
    enumeration fails, or nothing matches. Fail-safe: None is exactly today's
    behavior. ``list_output``/``discrete_vram_mb`` are injectable for tests.
    """
    if discrete_vram_mb is None:
        try:
            from intergen.hardware import HardwareDetector
            det = HardwareDetector()
            vendor, _model, vram_mb = det._detect_gpu()
            if not det._is_discrete_capable(vendor, vram_mb):
                return None
            discrete_vram_mb = vram_mb
        except Exception:
            return None
    if not discrete_vram_mb:
        return None

    if list_output is None:
        if server is None:
            server = shutil.which("llama-server") or ENGINE_SERVER_PATHS["vulkan"]
        try:
            proc = subprocess.run([server, "--list-devices"],
                                  capture_output=True, text=True, timeout=30)
            list_output = (proc.stdout or "") + (proc.stderr or "")
        except (OSError, subprocess.TimeoutExpired):
            return None

    candidates: list[tuple[str, str | None]] = []
    for m in _DEVICE_LINE_RE.finditer(list_output):
        total = int(m.group("total"))
        if abs(total - discrete_vram_mb) <= discrete_vram_mb * _DEVICE_VRAM_TOLERANCE:
            candidates.append((m.group("name"), m.group("pci")))
    if not candidates:
        return None

    for name, pci in candidates:
        if pci is not None and _pci_drives_display(pci) is False:
            return name
    return candidates[0][0]
