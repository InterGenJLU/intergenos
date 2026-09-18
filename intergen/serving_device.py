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
import logging
import os
import re
import shutil
import subprocess
from typing import NamedTuple

log = logging.getLogger(__name__)

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

# The CUDA build is compiled for a DECLARED list of NVIDIA architectures, in
# cmake's CMAKE_CUDA_ARCHITECTURES vocabulary. The list is written once in
# packages/compute/llama-cpp-cuda/package.yml (`gpu_targets`), the recipe
# passes it to cmake as -DCMAKE_CUDA_ARCHITECTURES, and the recipe also
# installs it here so the runtime can read the same list rather than carry a
# second copy that drifts — the same one-source-of-truth shape the HIP variant
# uses, at this engine's own prefix.
CUDA_GPU_TARGETS_PATH = "/opt/llama-cpp-cuda/share/llama-cpp-cuda/gpu-targets"

# Where the amdgpu kernel driver publishes each compute node's architecture.
KFD_TOPOLOGY_NODES = "/sys/class/kfd/kfd/topology/nodes"

# A CMAKE_CUDA_ARCHITECTURES entry: a compute-capability number, an optional
# 'a' marking it architecture-SPECIFIC, and an optional kind. A bare number
# means cmake's default (both compiled kernels and PTX), which is why the kind
# group is optional rather than required.
_CUDA_TARGET_RE = re.compile(
    r"^(?P<num>\d+)(?P<spec>a)?(?:-(?P<kind>real|virtual))?$")

# nvidia-smi's csv rows: "index, name, compute_cap, pci.bus_id". The tool
# prints the PCI domain in its eight-digit form ("00000000:01:00.0") while
# sysfs and ggml's --list-devices both use four ("0000:01:00.0"), so the
# domain is normalised to the sysfs spelling — a device id that did not match
# the one every other reader here uses would silently never join up.
_SMI_LINE_RE = re.compile(
    r"^\s*\d+\s*,\s*[^,]*,\s*(?P<major>\d+)\.(?P<minor>\d+)\s*,\s*"
    r"(?P<domain>[0-9a-fA-F]+):(?P<bus>[0-9a-fA-F]{2}):"
    r"(?P<dev>[0-9a-fA-F]{2})\.(?P<fn>[0-7])\s*$", re.MULTILINE)

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
#   nvidia: CUDA first, Vulkan the floor — decided 2026-09-15. The first-login
#   NVIDIA offer installs the proprietary driver, the CUDA toolkit and the
#   CUDA engine build for exactly one purpose: that the CUDA engine serves. An
#   offer whose acceptance changes nothing about which engine serves is a stub
#   (the shipped Vulkan-first row was measured on an installed machine
#   2026-09-10: the CUDA build worked and was never used). The CUDA rung is
#   taken only when its build AND the proprietary driver are present
#   (:func:`cuda_is_usable_here`) — the CUDA build cannot serve on the open
#   kernel driver, so a present binary alone is not a usable engine. The
#   earlier measurement stands on record: 2026-08-04 on a GeForce RTX 3070 Ti
#   Laptop (cc 8.6, driver 580.159.04, 9B Q4_K_M, same source pin both
#   engines, two orderings agreeing) the Vulkan engine was 3–7 % faster than
#   the CUDA engine on every metric taken (pp512 −3.1 %, pp2048 −4.2 %,
#   tg128 −6.5 %; the driver reports NV_coopmat2, so Vulkan also reaches the
#   tensor cores). That is a speed delta, not a correctness one, and the
#   decision rests on the offer meaning what it says; one config line
#   ("llama_server.engine": "vulkan") keeps the measured-faster engine for
#   anyone who wants it. This entry must not be reordered without a recorded
#   decision.
ENGINE_PREFERENCE: dict[str, list[str]] = {
    "amd":    ["hip", "vulkan"],
    "nvidia": ["cuda", "vulkan"],
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

    Comments are stripped LINE-WISE: everything from a ``#`` to the end of that
    line is dropped before the rest is split. Dropping only the TOKENS that
    begin with ``#`` left every following word on the line in the set, so a
    record opening ``# written by the recipe`` declared "by", "recipe", "the"
    and "written" as architectures alongside the real ones. That reads as noise
    and is not: this set is intersected with the architectures actually detected
    on the machine, and compared against the architecture of the card that will
    be pinned, so a comment that MENTIONS an architecture the build dropped
    would declare it as carried. The refusal this feeds is what keeps the engine
    off a card whose device code the build does not contain, and a wrong "yes"
    there is a crash at model load.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError:
        return set()
    targets = set()
    for line in raw.splitlines():
        line = line.split("#", 1)[0]
        for chunk in line.replace(";", " ").replace(",", " ").split():
            chunk = chunk.strip()
            if chunk:
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

    THIS IS THE MACHINE-LEVEL QUESTION, and on a multi-card machine it is the
    wrong one to decide the engine with: "some card here is covered" is not
    "the card the daemon will pin is covered". The engine choice therefore asks
    :func:`hip_supports_serving_device`, which asks about the card, and falls
    back to this answer only when no card can be identified. This function
    stays because that fallback needs it, and because the installer's
    first-boot offer has no pin to ask about yet.
    """
    detected = detect_amd_gfx_targets(topology_root)
    if not detected:
        return None
    supported = hip_build_gpu_targets(targets_path)
    if not supported:
        return None
    return bool(detected & supported)


def cuda_build_gpu_targets(path: str = CUDA_GPU_TARGETS_PATH) -> set[str]:
    """The architectures the installed CUDA build actually carries code for.

    Read from the file the CUDA recipe installs. An empty set means the file
    is absent or unreadable, which callers treat as unknown — an engine built
    before this record existed must not be read as an engine that supports
    nothing.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError:
        return set()
    targets = set()
    for line in raw.splitlines():
        # A comment runs to the end of its LINE. Dropping only the tokens that
        # begin with '#' would keep every remaining word of the comment as a
        # target — "# written by the recipe" would contribute four of them.
        line = line.split("#", 1)[0]
        for chunk in line.replace(";", " ").replace(",", " ").split():
            chunk = chunk.strip()
            if chunk:
                targets.add(chunk)
    return targets


def _cuda_targets_cover(compute_cap: int, targets: "set[str]") -> bool:
    """Whether a build compiled for ``targets`` can serve a card of this
    compute capability, expressed as cmake does — 8.6 is 86, 12.0 is 120.

    WHY THIS IS NOT SET MEMBERSHIP, which is what the HIP side does. The CUDA
    target vocabulary distinguishes compiled kernels from PTX:

      86-real       SASS for exactly 8.6. Machine code; it runs on 8.6 and on
                    nothing else, because SASS is not forwards compatible.
      80-virtual    PTX for 8.0. The driver JIT-compiles it at first load for
                    8.0 and for any newer architecture, at the cost of a
                    one-time pause.
      120a-real     the 'a' means architecture-SPECIFIC: Blackwell's FP4
                    tensor-core instructions, which upstream documents as NOT
                    forwards compatible. It covers 12.0 and stops there.
      86            a bare number is cmake's default, meaning both of the
                    first two forms for that number.

    So the question is not "is my number listed" but "is there an entry whose
    code this card can execute": an exact entry of either kind, or a
    non-specific ``-virtual`` entry BELOW this card that the driver can JIT
    forward from. A machine at 9.0 with 80-virtual in the list serves fine,
    and a gate that demanded exact membership would refuse it.
    """
    for token in targets:
        match = _CUDA_TARGET_RE.match(token.strip())
        if not match:
            continue
        num = int(match.group("num"))
        if num == compute_cap:
            return True
        # PTX JITs forward, but only from an entry that is not tied to one
        # architecture. An 'a' entry below this card carries instructions this
        # card may not have.
        if (match.group("kind") == "virtual" and not match.group("spec")
                and num < compute_cap):
            return True
    return False


def _parse_compute_caps(text: str) -> "dict[str, int]":
    """Per-card compute capability keyed by PCI id, parsed from nvidia-smi's
    csv rows. A row that does not parse contributes nothing, so a tool that
    printed "No devices were found" yields an empty mapping rather than a
    confident-looking wrong answer."""
    caps: dict[str, int] = {}
    for match in _SMI_LINE_RE.finditer(text or ""):
        cap = int(match.group("major")) * 10 + int(match.group("minor"))
        domain = match.group("domain")[-4:].rjust(4, "0").lower()
        pci = (f"{domain}:{match.group('bus').lower()}:"
               f"{match.group('dev').lower()}.{match.group('fn')}")
        caps[pci] = cap
    return caps


def detect_nvidia_compute_caps(smi_path: "str | None" = None
                               ) -> "dict[str, int]":
    """Every NVIDIA card's compute capability, keyed by PCI id.

    Unlike the AMD side, which reads architectures straight out of the kernel's
    KFD topology, no kernel interface publishes an NVIDIA card's compute
    capability — ``/proc/driver/nvidia/gpus/*/information`` carries the model
    name, the firmware and the bus location, but not the number this gate
    needs. It therefore comes from the driver's own query tool, which is
    acceptable only because the CUDA engine already requires that same
    proprietary driver to serve at all.

    An empty mapping means "nothing was readable" — no tool, a tool that
    failed, or output that did not parse — and callers must treat it as
    unknown, never as "unsupported".
    """
    exe = smi_path or shutil.which("nvidia-smi")
    if not exe:
        return {}
    try:
        completed = subprocess.run(
            [exe, "--query-gpu=index,name,compute_cap,pci.bus_id",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return {}
    if completed.returncode != 0:
        return {}
    return _parse_compute_caps(completed.stdout)


def cuda_card_support(targets_path: str = CUDA_GPU_TARGETS_PATH,
                      caps: "dict[str, int] | None" = None
                      ) -> "dict[str, bool | None]":
    """Per-card verdict, keyed by PCI id: can the installed CUDA build serve
    on THIS card?

    Per-card rather than per-machine because a multi-GPU box can carry cards
    of different generations, and the serving model pins exactly one of them
    (:func:`select_serving_device`). "The machine supports CUDA" is not a
    usable answer when the card the pin lands on is the one that does not; a
    caller reporting a refusal can name the card and the number it is missing.
    A value of ``None`` means the build's target list was unreadable, so
    nothing is known about that card either way.
    """
    if caps is None:
        caps = detect_nvidia_compute_caps()
    targets = cuda_build_gpu_targets(targets_path)
    if not targets:
        return {pci: None for pci in caps}
    return {pci: _cuda_targets_cover(cap, targets) for pci, cap in caps.items()}


def cuda_is_supported_here(targets_path: str = CUDA_GPU_TARGETS_PATH,
                           caps: "dict[str, int] | None" = None
                           ) -> "bool | None":
    """Whether the installed CUDA build has code for at least one of this
    machine's NVIDIA cards.

    Returns True when at least one card is covered, False when cards were
    detected and NONE are, and None when either side is unknown — the same
    three-valued discipline :func:`hip_is_supported_here` keeps, and for the
    same reason: "I could not tell" and "I checked and it will not work" have
    different correct responses, and collapsing them would either strand every
    machine whose driver state is unusual or claim support on machines that
    have none.

    At least one card is enough because selection pins ONE card, and
    :func:`select_serving_device` prefers a servable one; the per-card detail
    for a caller that needs to say WHICH is :func:`cuda_card_support`.
    """
    if caps is None:
        caps = detect_nvidia_compute_caps()
    if not caps:
        return None
    targets = cuda_build_gpu_targets(targets_path)
    if not targets:
        return None
    return any(_cuda_targets_cover(cap, targets) for cap in caps.values())


def _pci_address_from_location(domain: int, location_id: int) -> str:
    """The PCI address a KFD node's ``domain`` and ``location_id`` name.

    The amdgpu driver packs the compute node's PCI bus/device/function into
    one integer — bus in bits 8..15, device in bits 3..7, function in bits
    0..2 — and publishes the PCI domain as its own property. The result is
    written in the ``dddd:bb:dd.f`` form the engine's ``--list-devices`` lines
    carry, so a kernel-side address and an engine-side address compare as
    plain strings with no second format to keep in step.
    """
    bus = (location_id >> 8) & 0xFF
    device = (location_id >> 3) & 0x1F
    function = location_id & 0x7
    return f"{domain:04x}:{bus:02x}:{device:02x}.{function}"


def amd_gfx_targets_by_pci(topology_root: str = KFD_TOPOLOGY_NODES
                           ) -> dict[str, str]:
    """Each AMD compute node's gfx architecture, keyed by its PCI address.

    This is :func:`detect_amd_gfx_targets` with the cards kept apart instead
    of merged into one set, which is what lets a caller ask about ONE card.
    Same source, same reader, no tool and no ROCm userspace: the driver's KFD
    topology publishes ``gfx_target_version`` and ``location_id`` in the same
    ``properties`` file.

    Nodes with no architecture are skipped, which is how the CPU node the
    driver always publishes (``gfx_target_version 0``) stays out. An empty
    dict means nothing was readable, which callers must treat as unknown
    rather than as "unsupported".
    """
    found: dict[str, str] = {}
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
        values: dict[str, int] = {}
        for line in text.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] in ("gfx_target_version",
                                                "location_id", "domain"):
                try:
                    values[parts[0]] = int(parts[1])
                except ValueError:
                    pass
        name = _gfx_name(values.get("gfx_target_version", 0))
        if not name or "location_id" not in values:
            continue
        found[_pci_address_from_location(values.get("domain", 0),
                                         values["location_id"])] = name
    return found


class HipDeviceSupport(NamedTuple):
    """The HIP gate's answer, with what it rests on.

    ``supported`` is the three-valued verdict — True, False, or None for "could
    not tell". ``pci_id`` and ``gfx`` name the card the answer is about, and are
    None when the answer had to fall back to the machine-level question.
    ``targets`` is the build's declared architecture list, and ``reason`` is a
    sentence naming the card and what is missing, for the log the refusal
    writes: a machine that quietly serves on the wrong engine is the failure
    this whole gate exists to make visible.
    """
    supported: bool | None
    pci_id: str | None
    gfx: str | None
    targets: frozenset[str]
    reason: str


def hip_supports_serving_device(server: str | None = None,
                                device_pin: str | None = None,
                                list_output: str | None = None,
                                discrete_vram_mb: int | None = None,
                                sysfs_root: str = "/sys",
                                topology_root: str | None = None,
                                targets_path: str | None = None
                                ) -> HipDeviceSupport:
    """Whether the HIP build carries device code for the card it would PIN.

    WHY THIS EXISTS RATHER THAN :func:`hip_is_supported_here`. That check is
    machine-level: it says yes when any architecture the machine has appears in
    the build's target list. The device pin is per-card — the serving model
    takes ONE card. On a machine with a gfx1100 card and a gfx1102 card, and a
    build covering gfx1102 and not gfx1100, the machine-level check says
    "supported" on the strength of the gfx1102 card and the daemon then pins
    the gfx1100 card, for which that build has no device code. llama-server
    segfaults at model load, and a machine that would have served on Vulkan
    serves nothing. Measured on a two-card AMD workstation 2026-09-18.

    The card asked about comes from THE SAME selection that produces the pin
    (:func:`select_serving_device_and_pci`, enumerating with the HIP binary),
    so the gate and the launch can never describe different cards. ``server``
    names that binary; the engine's own path is the right value, because device
    names and addresses are backend-local.

    ``device_pin`` is the ``llama_server.device`` config value. An operator who
    names a card by its ggml name is pinning THAT card, and the gate has to ask
    about the card that will actually be served on — asking the automatic
    selector instead would refuse HIP over a card the operator excluded, or
    accept it over a card they did not choose. The name is resolved to an
    address exactly as the daemon resolves it (:func:`pci_for_device_name`),
    and a name that does not resolve identifies no card, so the answer falls
    back rather than guessing.

    THE THREE-VALUED ANSWER IS PRESERVED, and every unknown falls back to the
    machine-level answer rather than to a refusal: an engine build that names
    no PCI addresses, a card absent from the topology, an unreadable topology
    or an unreadable target list all leave today's behaviour exactly as it was.
    Only a MEASURED "this card's architecture is not in the list" refuses.

    ``topology_root`` and ``targets_path`` default to the module constants and
    are resolved when called, not when defined, so a caller — or a test — that
    replaces a constant gets the replacement.
    """
    if topology_root is None:
        topology_root = KFD_TOPOLOGY_NODES
    if targets_path is None:
        targets_path = HIP_GPU_TARGETS_PATH
    if server is None:
        server = ENGINE_SERVER_PATHS["hip"]

    targets = hip_build_gpu_targets(targets_path)
    declared = frozenset(targets)

    def _machine_level(why: str) -> HipDeviceSupport:
        verdict = hip_is_supported_here(topology_root, targets_path)
        return HipDeviceSupport(verdict, None, None, declared,
                                f"{why}; the machine-level answer stands "
                                f"({verdict!r})")

    if not declared:
        return _machine_level("the installed HIP build declares no "
                              "architecture list")

    pinned_by_hand = bool(device_pin
                          and device_pin.strip().lower() not in ("auto", ""))
    if pinned_by_hand:
        pci_id = pci_for_device_name(device_pin.strip(),
                                     list_output=list_output, server=server)
    else:
        _name, pci_id = select_serving_device_and_pci(
            list_output=list_output, discrete_vram_mb=discrete_vram_mb,
            server=server, sysfs_root=sysfs_root)
    if not pci_id:
        return _machine_level("the card that would be pinned has no resolvable "
                              "PCI address")

    by_pci = amd_gfx_targets_by_pci(topology_root)
    gfx = by_pci.get(pci_id.lower())
    if gfx is None:
        return _machine_level(f"no compute node reports the architecture of "
                              f"the card at PCI {pci_id}")

    if gfx in declared:
        return HipDeviceSupport(
            True, pci_id, gfx, declared,
            f"the card that would be pinned (PCI {pci_id}, {gfx}) is in the "
            f"installed HIP build's architecture list "
            f"({';'.join(sorted(declared))})")
    return HipDeviceSupport(
        False, pci_id, gfx, declared,
        f"the card that would be pinned (PCI {pci_id}) is {gfx}, which the "
        f"installed HIP build has no device code for: it declares "
        f"{';'.join(sorted(declared))}")


def cuda_is_usable_here(drm_root: "str | os.PathLike" = "/sys/class/drm") -> bool:
    """Whether the CUDA engine build can serve on this machine: an NVIDIA card
    is bound to NVIDIA's own kernel driver (read from sysfs by
    :func:`intergen.model_choice.detect_driver_state`, which never raises).

    Present is not usable: the CUDA build needs the proprietary driver's
    runtime behind it, and on the open driver (nouveau) or with no NVIDIA card
    it cannot serve. Unreadable driver state is "not usable" — the Vulkan
    floor then serves, and nothing is guessed. ``drm_root`` is injectable so
    the three states are provable without the hardware.
    """
    try:
        from intergen.model_choice import detect_driver_state
        return bool(detect_driver_state(drm_root).proprietary_nvidia)
    except Exception:
        return False


def _detect_vendor() -> str | None:
    """The hardware detector's GPU vendor string for this machine ("amd",
    "nvidia", "intel", …) or None when detection fails. The ONE vendor
    detection behind both the engine choice and the engine ladder, so the two
    can never disagree about which machine they are on."""
    try:
        from intergen.hardware import HardwareDetector
        vendor, _model, _vram_mb = HardwareDetector()._detect_gpu()
        return vendor
    except Exception:
        return None


def select_serving_engine(vendor: str | None = None,
                          engine_pin: str | None = None,
                          device_pin: str | None = None) -> tuple[str, str]:
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

    ``device_pin`` is the ``llama_server.device`` config value, passed through
    to the HIP architecture gate so that gate asks about the card that will
    actually be served on. It is not used for anything else here: which card
    serves is still decided after this function returns, by the selector or by
    the pin itself.
    """
    if engine_pin and engine_pin not in ("auto", ""):
        pin = engine_pin.strip().lower()
        return pin, ENGINE_SERVER_PATHS.get(pin, "")

    if vendor is None:
        vendor = _detect_vendor()

    for engine in ENGINE_PREFERENCE.get(vendor or "", _DEFAULT_PREFERENCE):
        path = ENGINE_SERVER_PATHS[engine]
        if not (os.path.isfile(path) and os.access(path, os.X_OK)):
            continue
        # Present is not the same as usable. The HIP build carries device code
        # only for the architectures it was compiled for, and on an AMD GPU
        # outside that list llama-server segfaults at model load — so a machine
        # that would have served fine on Vulkan crashes instead. The question
        # is asked about the CARD THIS ENGINE WOULD PIN, not about the machine:
        # on a two-card box "some card here is covered" let the daemon pin the
        # card that was not (measured 2026-09-18). Only a MEASURED "no" skips
        # the engine; an unresolvable pin, an unreadable topology or a missing
        # target list leave the preference alone, because refusing on "I could
        # not tell" would strand every machine whose driver state is unusual.
        if engine == "hip":
            support = hip_supports_serving_device(server=path,
                                                  device_pin=device_pin)
            if support.supported is False:
                log.info("declining the HIP engine: %s", support.reason)
                continue
        # The CUDA build serves only behind the proprietary driver; on the
        # open driver a present binary is not a usable engine (decided
        # 2026-09-15, the preference table's nvidia row).
        if engine == "cuda" and not cuda_is_usable_here():
            continue
        # The right driver is not the same as the right architecture. The CUDA
        # build carries code for a declared target list, and a card outside it
        # — a Volta part under CUDA 13, say — has neither compiled kernels nor
        # PTX to JIT from, so the engine cannot serve however good the driver
        # is. Only a MEASURED "no" skips it, the same rule the HIP gate above
        # follows: an unreadable capability or a build that installed no target
        # record leaves the preference alone.
        if engine == "cuda" and cuda_is_supported_here() is False:
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

    The architecture gate is applied here too, for both variants, and asks
    the same per-card question the engine choice asks, so a HIP or CUDA
    build that measurably cannot run on the card this machine would pin is
    not offered as a rung.
    """
    ladder: list[tuple[str, str]] = []
    if vendor is None:
        # Detect exactly as select_serving_engine does. A caller that names
        # no vendor used to get the vendor-less default ladder — Vulkan alone
        # — so on an NVIDIA machine a working CUDA build was never a rung and
        # a Vulkan failure read "ladder exhausted" (measured 2026-09-10).
        vendor = _detect_vendor()
    order = list(ENGINE_PREFERENCE.get(vendor or "", _DEFAULT_PREFERENCE))
    for engine in order + ["vulkan"]:
        if any(e == engine for e, _ in ladder):
            continue
        path = ENGINE_SERVER_PATHS.get(engine, "")
        if not path or not (os.path.isfile(path) and os.access(path, os.X_OK)):
            continue
        if engine == "hip":
            support = hip_supports_serving_device(server=path)
            if support.supported is False:
                log.info("HIP is not a rung on this machine: %s",
                         support.reason)
                continue
        if engine == "cuda" and not cuda_is_usable_here():
            continue
        if engine == "cuda" and cuda_is_supported_here() is False:
            continue
        ladder.append((engine, path))
    return ladder


def next_engine_after(failed_engine: str | None,
                      vendor: str | None = None,
                      tried: "set[str] | frozenset[str] | None" = None
                      ) -> tuple[str, str] | None:
    """The next UNTRIED rung of this machine's ladder, in preference order,
    or None when every rung has been tried.

    ``failed_engine`` is the engine that just failed; ``tried`` names the
    engines already attempted in this sequence (the caller accumulates it and
    clears it on a successful start). Both are excluded. The rungs are walked
    in preference order, not "below the failed one": the floor engine can be
    the first to fail (a config pin, or a machine whose preferred engine was
    installed after it) and an untried higher rung is still an engine this
    machine has. Returning None is the honest end of the ladder: every engine
    this machine has has now been tried, and the caller must fail loudly
    rather than loop — the tried set only grows, so this terminates.
    """
    ladder = engine_ladder(vendor)
    if not ladder:
        return None
    excluded = set(tried or ())
    if failed_engine:
        excluded.add(failed_engine)
    for engine, path in ladder:
        if engine not in excluded:
            return engine, path
    return None


# DRM connector types that are NOT a display sink. The kernel's connector
# type list (drm_connector_enum_list) names these types: Unknown, VGA, DVI-I,
# DVI-D, DVI-A, Composite, SVIDEO, LVDS, Component, DIN, DP, HDMI-A, HDMI-B,
# TV, eDP, Virtual, DSI, DPI, Writeback, SPI, USB. Every one of them is a
# physical or guest-visible display sink except WRITEBACK: a writeback
# connector is the capture sink a compositor renders INTO (the kernel reports
# its status as "connected" whenever the driver exposes it — measured on a
# two-card machine 2026-09-15, "Writeback-2" connected on the card with every
# DP disconnected). Counting it made a display-free card read as driving a
# display, so the serving model landed on the card painting the desktop. A
# "Virtual" connector IS a display (a virtual machine's guest screen) and is
# counted; "Unknown" is counted too, because failing toward "driving a
# display" keeps serving OFF a card whose state is unclear.
NON_DISPLAY_CONNECTOR_TYPES: frozenset[str] = frozenset({"Writeback"})


def _connector_type(connector_dir_name: str) -> str:
    """The connector TYPE encoded in a ``<sysfs>/class/drm/<card>-<TYPE>-<n>``
    directory name — ``card0-HDMI-A-1`` is ``HDMI-A``, ``card0-Writeback-2``
    is ``Writeback``. The type is the name between the card and the trailing
    index; the kernel writes no separate type attribute for a connector."""
    rest = connector_dir_name.split("-", 1)[1] if "-" in connector_dir_name else ""
    return rest.rsplit("-", 1)[0] if "-" in rest else rest


def _pci_drives_display(pci_id: str, sysfs_root: str = "/sys") -> bool | None:
    """Whether the GPU at ``pci_id`` is driving a connected display.

    Resolution is through the kernel's own records, no tools:
    ``<sysfs>/bus/pci/devices/<id>/drm/`` names the card's DRM node(s), and
    each connector's ``<sysfs>/class/drm/<card>-*/status`` says whether a
    display is attached. Returns ``True`` when any DISPLAY connector on the
    card reports "connected", ``False`` when the card exists and none do, and
    ``None`` when the mapping cannot be read (no DRM node, no such PCI
    device) — the caller treats ``None`` as "unknown", never as an answer.
    Connectors whose type is in :data:`NON_DISPLAY_CONNECTOR_TYPES` (the
    kernel's writeback capture sinks) are not displays and are skipped.
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
            if (_connector_type(os.path.basename(os.path.dirname(status_path)))
                    in NON_DISPLAY_CONNECTOR_TYPES):
                continue
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


def display_state_words(pci_id: str, sysfs_root: str = "/sys") -> str:
    """The display state of the card at ``pci_id`` in the words the launch log
    prints beside the chosen device: "driving a display", "display-free", or
    "display state unknown" — the three answers of :func:`_pci_drives_display`,
    so the log names the chosen card AND what the choice rested on."""
    state = _pci_drives_display(pci_id, sysfs_root)
    if state is True:
        return "driving a display"
    if state is False:
        return "display-free"
    return "display state unknown"


def _select_serving_candidate(list_output: str | None = None,
                              discrete_vram_mb: int | None = None,
                              server: str | None = None,
                              sysfs_root: str = "/sys"
                              ) -> tuple[str, str | None, int | None] | None:
    """Pick the ggml device the SERVING model should pin on a multi-GPU box.

    Returns ``(ggml name, PCI address or None, total MiB or None)`` — ONE
    selection, read several ways by the public wrappers below, so the name, the
    address and the card's SIZE can never come from different cards. It used to
    return the name alone and throw the address away, which left the power hold
    with nothing to aim at; it then threw the size away, which left the offload
    plan measuring the hardware detector's most-capable card while the model
    went onto whichever card was pinned.

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
    behavior. ``list_output``/``discrete_vram_mb`` are injectable for tests,
    and so is ``sysfs_root``: the display-free check reads the kernel's
    records under it, so a test hands in a fake tree and gets the same answer
    on every machine, instead of an answer that depends on which connector of
    the machine running the tests happens to have a monitor on it.
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

    candidates: list[tuple[str, str | None, int | None]] = []
    for m in _DEVICE_LINE_RE.finditer(list_output):
        total = int(m.group("total"))
        if abs(total - discrete_vram_mb) <= discrete_vram_mb * _DEVICE_VRAM_TOLERANCE:
            candidates.append((m.group("name"), m.group("pci"), total))
    if not candidates:
        return None

    for name, pci, total in candidates:
        if pci is not None and _pci_drives_display(pci, sysfs_root) is False:
            return (name, pci, total)
    return candidates[0]


def select_serving_device(list_output: str | None = None,
                          discrete_vram_mb: int | None = None,
                          server: str | None = None,
                          sysfs_root: str = "/sys") -> str | None:
    """The ggml device NAME the serving model should pin to, or None.

    See :func:`_select_serving_candidate` for the policy. This and
    :func:`select_serving_device_pci` are two readings of ONE selection, so the
    name and the address can never describe different cards.
    """
    chosen = _select_serving_candidate(list_output, discrete_vram_mb, server,
                                       sysfs_root)
    return chosen[0] if chosen else None


def select_serving_device_pci(list_output: str | None = None,
                              discrete_vram_mb: int | None = None,
                              server: str | None = None,
                              sysfs_root: str = "/sys") -> str | None:
    """The PCI address of the card :func:`select_serving_device` picked.

    Returns None when there is no pin, and ALSO when the chosen card carries no
    PCI suffix — an engine build without the in-tree list-devices patch names
    devices but not addresses, and a guess at which card is which would hold
    the wrong card's power on. None is the fail-safe: no hold, today's
    behaviour exactly.
    """
    chosen = _select_serving_candidate(list_output, discrete_vram_mb, server,
                                       sysfs_root)
    return chosen[1] if chosen else None


def select_serving_device_and_pci(list_output: str | None = None,
                                  discrete_vram_mb: int | None = None,
                                  server: str | None = None,
                                  sysfs_root: str = "/sys"
                                  ) -> tuple[str | None, str | None]:
    """Both readings of ONE selection: ``(ggml name, PCI address)``.

    This is what a caller that needs both should use. Calling the two
    single-value wrappers in turn would run the engine's ``--list-devices``
    TWICE — a second llama-server launch on the daemon's boot path for an
    answer it already had — and would also let two independent selections
    disagree. Either element is None when it is unavailable; ``(None, None)``
    is no pin at all, which is llama.cpp's own default behaviour.
    """
    chosen = _select_serving_candidate(list_output, discrete_vram_mb, server,
                                       sysfs_root)
    return (chosen[0], chosen[1]) if chosen else (None, None)


def select_serving_device_name_pci_and_vram(
        list_output: str | None = None,
        discrete_vram_mb: int | None = None,
        server: str | None = None,
        sysfs_root: str = "/sys"
        ) -> tuple[str | None, str | None, int | None]:
    """All three readings of ONE selection: ``(ggml name, PCI address, MiB)``.

    The third element is the SIZE OF THE CARD THAT WILL BE PINNED, as the
    engine's own ``--list-devices`` line reports it. It exists because the
    offload plan — whether the model fits, and how many layers go on the card —
    was computed from the hardware detector's MOST-CAPABLE card while the model
    went onto whichever card this selection pinned. On a machine whose cards
    differ in size those are different numbers: measured on a two-card
    workstation 2026-09-18, the plan reported "card 20464 MiB" and declared a
    comfortable fit while the model was placed on the 8176 MiB card.

    Taking the size from the SAME selection that produces the pin is what makes
    them impossible to disagree; reading it from sysfs separately would be a
    second route to the same fact, able to drift. It is also backend-neutral:
    every engine build prints its own devices' totals, so this works for the
    CUDA and Vulkan builds exactly as it does for HIP.

    None in any position means that part is unavailable, and the caller must
    fall back rather than guess — for the size, that means using the detected
    figure it used before, and SAYING which one it used.
    """
    chosen = _select_serving_candidate(list_output, discrete_vram_mb, server,
                                       sysfs_root)
    return chosen if chosen else (None, None, None)


def pci_and_vram_for_device_name(device_name: str,
                                 list_output: str | None = None,
                                 server: str | None = None
                                 ) -> tuple[str | None, int | None]:
    """``(PCI address, total MiB)`` for the device the engine calls
    ``device_name`` — the OPERATOR PIN's reading of the same device line.

    A card named by hand in ``llama_server.device`` is the card the model goes
    onto, so it is the card the offload plan has to be measured against. The
    match is the ggml name EXACTLY as the engine prints it, with the same
    no-guessing rule :func:`pci_for_device_name` documents: a name that is not
    in the output yields ``(None, None)``, and a line that carries no PCI
    suffix still yields its size, because the size is on every line while the
    address needs the in-tree list-devices patch.
    """
    if list_output is None:
        if server is None:
            server = shutil.which("llama-server") or ENGINE_SERVER_PATHS["vulkan"]
        try:
            proc = subprocess.run([server, "--list-devices"],
                                  capture_output=True, text=True, timeout=30)
            list_output = (proc.stdout or "") + (proc.stderr or "")
        except (OSError, subprocess.TimeoutExpired):
            return (None, None)
    for m in _DEVICE_LINE_RE.finditer(list_output):
        if m.group("name") == device_name:
            return (m.group("pci"), int(m.group("total")))
    return (None, None)


def pci_for_device_name(device_name: str, list_output: str | None = None,
                        server: str | None = None) -> str | None:
    """The PCI address of the device the engine calls ``device_name``.

    This exists for the OPERATOR PIN: ``llama_server.device`` set to a literal
    ggml name is supreme over the selector, so the selector never runs and its
    address is never computed — and without an address that box gets no power
    hold, which is the very machine class the hold was written for (a
    multi-GPU box, pinned by hand).

    The match is the ggml name EXACTLY as the engine prints it, and nothing
    else: no prefix match, no case folding, no ordinal arithmetic. A name that
    is not in the output, or a line that carries no PCI suffix, yields None —
    no hold, today's behaviour exactly. Holding a card resolved by a guess
    would suspend or wake the wrong one.

    ``server`` names the binary to enumerate with, and it must be the same
    engine binary that will launch, because device names are backend-local.
    """
    if list_output is None:
        if server is None:
            server = shutil.which("llama-server") or ENGINE_SERVER_PATHS["vulkan"]
        try:
            proc = subprocess.run([server, "--list-devices"],
                                  capture_output=True, text=True, timeout=30)
            list_output = (proc.stdout or "") + (proc.stderr or "")
        except (OSError, subprocess.TimeoutExpired):
            return None
    for m in _DEVICE_LINE_RE.finditer(list_output):
        if m.group("name") == device_name:
            return m.group("pci")
    return None


# ── Runtime power management of the card that is serving ────────────────────
#
# WHY THIS IS HERE AT ALL. A discrete GPU with nothing plugged into it is left
# at power/control = "auto", so the kernel runtime-suspends it whenever it is
# idle. Starting or stopping a served model touches that card, the kernel
# runtime-RESUMES it, and the resume announces a hotplug on every connector —
# which the compositor reads as "the device changed", frees its planes, CRTCs
# and connectors, and rebuilds the desktop. The person watching sees their
# windows move. Holding the card ON for exactly as long as a model is on it
# means the wake never has to happen.
#
# THE PATH IS THE PCI DEVICE NODE, measured: runtime_status reads "unsupported"
# on every drm/cardN node and "active" on the PCI device node, so
# <sysfs>/bus/pci/devices/<id>/power/control is the file that means anything.
#
# EVERY FUNCTION BELOW IS QUIET ON REFUSAL. power/control ships mode 0644 owned
# by root, and the daemon runs as an ordinary user, so until the udev rule this
# package installs has been applied the write simply does not land. That is the
# state of every box between installs — an ordinary outcome, never an error and
# never an exception. sysfs_root is injectable so the whole path is provable
# against a temporary directory, exactly like the display check above.


def runtime_pm_control_path(pci_id: str, sysfs_root: str = "/sys") -> str:
    """The file whose contents decide whether the kernel may suspend a card."""
    return os.path.join(sysfs_root, "bus", "pci", "devices", pci_id,
                        "power", "control")


def read_runtime_pm(pci_id: str, sysfs_root: str = "/sys") -> str | None:
    """The card's current power/control value, or None if it cannot be read."""
    try:
        with open(runtime_pm_control_path(pci_id, sysfs_root),
                  encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def hold_runtime_pm_on(pci_id: str, sysfs_root: str = "/sys") -> str | None:
    """Hold the card ON, and return the value that was there to put back.

    Returns None when nothing was changed — the card is absent, the value
    cannot be read, or the write was refused — and in that case the caller has
    nothing to restore. A card already at "on" returns "on", so releasing it
    later leaves it on rather than handing the machine a worse state than it
    started in.
    """
    prior = read_runtime_pm(pci_id, sysfs_root)
    if prior is None:
        return None
    if prior == "on":
        return prior
    try:
        with open(runtime_pm_control_path(pci_id, sysfs_root), "w",
                  encoding="utf-8") as fh:
            fh.write("on")
    except OSError:
        return None
    return prior


def release_runtime_pm(pci_id: str, prior: str | None,
                       sysfs_root: str = "/sys") -> bool:
    """Put ``prior`` back, verbatim. True if it was written.

    ``prior`` of None means the hold never happened, so nothing is written —
    inventing a value here would set a card to a state no one asked for. A
    value this code does not recognise is restored AS IT WAS FOUND for the
    same reason.
    """
    if prior is None:
        return False
    try:
        with open(runtime_pm_control_path(pci_id, sysfs_root), "w",
                  encoding="utf-8") as fh:
            fh.write(prior)
    except OSError:
        return False
    return True
