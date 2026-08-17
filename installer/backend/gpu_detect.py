# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Display-controller detection, and the record the first-boot welcome reads.

What ships works on every machine with no choice made anywhere: the open
source graphics drivers (nouveau on NVIDIA, amdgpu on AMD, i915 on Intel)
are on the install medium, and the in-tree Vulkan build of the inference
engine serves the assistant on top of them. Two classes of hardware can do
better with software that is NOT allowed on the medium:

  * NVIDIA's proprietary driver, whose licence forbids redistribution;
  * the per-vendor compute engine builds (CUDA on NVIDIA, HIP/ROCm on AMD),
    which are mirror-only by design.

Neither can be installed while the installer runs, and the installer never
reaches the network. So the installer's whole job here is to WRITE DOWN what
it found, and the offer to install it is made on the first boot — by the
welcome application, where the package manager is present, the machine is on
a network, and the vendor's own licence gate can run with its full text on
the user's own machine.

Decided 2026-08-05: an installer page that showed a terminal command the user
had to remember across a reboot was not helping anyone. The offer belongs
where the install can actually happen. This module is what survived that
page: the detection, the ratified engine facts, and the record.

The engine ranking recorded here is the ratified per-vendor preference the
serving-engine selector uses (`intergen.serving_device.ENGINE_PREFERENCE`).
`_ENGINE_PREFERENCE` below is a copy carried so the installer does not
hard-depend on the assistant's package — the installer runs from a medium
that carries no assistant — and `tests/installer/test_gpu_detect.py` asserts
the two are identical, so the copy cannot drift.
"""

import json
import logging
import os

from installer.backend.packages import detect_display_pci_vendors

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hardware
# ---------------------------------------------------------------------------

# Display-controller PCI vendor ids, mapped to the vendor names the serving
# engine selector keys on. AMD ships display controllers under both of its
# vendor ids (1002 is the graphics line, 1022 the host bridge line that some
# APUs present their display function under).
_PCI_VENDOR_NAMES = {
    "10de": "nvidia",
    "1002": "amd",
    "1022": "amd",
    "8086": "intel",
}

# When a machine has more than one display controller — every laptop with
# switchable graphics — the discrete card is the one the upgrade paths are
# about, so a discrete vendor outranks the integrated one. Intel's parts are
# integrated in every machine this applies to.
_VENDOR_RANK = ["nvidia", "amd", "intel"]


def detect_gpu_vendor(pci_vendors=None):
    """The GPU vendor to record, or None.

    ``pci_vendors`` is the set of display-controller PCI vendor ids present
    on the machine; ``None`` means read them from the hardware, through the
    SAME probe the install-time hardware gate uses
    (:func:`installer.backend.packages.detect_display_pci_vendors`), so the
    record cannot name a driver the gate would refuse or stay silent about
    one it would accept.

    Returns ``None`` when nothing was detected. None is the honest answer for
    "the display controller could not be identified" and the first-boot offer
    stays silent on it — never a guess.
    """
    if pci_vendors is None:
        pci_vendors = detect_display_pci_vendors()
    names = {_PCI_VENDOR_NAMES[v] for v in pci_vendors
             if v in _PCI_VENDOR_NAMES}
    for vendor in _VENDOR_RANK:
        if vendor in names:
            return vendor
    return None


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------

# The ratified per-vendor engine preference. A COPY of
# intergen.serving_device.ENGINE_PREFERENCE, asserted equal to it by
# test_gpu_detect.TestEnginePreferenceMatchesTheSelector.
_ENGINE_PREFERENCE = {
    "amd":    ["hip", "vulkan"],
    "nvidia": ["vulkan", "cuda"],
}
_DEFAULT_PREFERENCE = ["vulkan"]

# The engine that ships on the medium. Every other entry in the preference
# table names a build that is mirror-only, so on a machine where nothing has
# been added this is the engine that serves, whatever the table prefers.
SHIPPED_ENGINE = "vulkan"

# The AMD architectures the HIP build is compiled for. A COPY of
# packages/compute/llama-cpp-hip/package.yml `gpu_targets`, asserted equal to it
# by test_gpu_detect.TestHipTargetsMatchTheRecipe.
#
# The copy exists because this code runs during the install, where the HIP
# package is not present — it is mirror-only — so the list it would otherwise be
# read from is not on the machine yet. That is also why the parity test matters:
# a recipe rebuilt for new architectures with this list left behind would make
# the installer offer HIP to hardware it has no code for.
HIP_GPU_TARGETS = ("gfx1100", "gfx1102", "gfx1201")

# Where the amdgpu kernel driver publishes each compute node's architecture. The
# reader below is a COPY of intergen.serving_device.detect_amd_gfx_targets,
# asserted equivalent by test_gpu_detect.TestGfxReaderMatchesTheSelector; the
# installer cannot import from the intergen package.
KFD_TOPOLOGY_NODES = "/sys/class/kfd/kfd/topology/nodes"


def _gfx_name(target_version):
    """A KFD ``gfx_target_version`` integer as its gfx name.

    ``major*10000 + minor*100 + step``, with minor and step spelled as single
    hex digits: 90012 is gfx90c, 110000 is gfx1100, 120001 is gfx1201.
    """
    if not isinstance(target_version, int) or target_version <= 0:
        return None
    major, rest = divmod(target_version, 10000)
    minor, step = divmod(rest, 100)
    if major <= 0 or minor > 15 or step > 15:
        return None
    return "gfx{}{:x}{:x}".format(major, minor, step)


def detect_amd_gfx_targets(topology_root=KFD_TOPOLOGY_NODES):
    """The gfx architecture of every AMD compute node the kernel reports.

    Empty means nothing was readable, which is "unknown", never "unsupported".
    """
    found = set()
    try:
        nodes = sorted(os.listdir(topology_root))
    except OSError:
        return found
    for node in nodes:
        props = os.path.join(topology_root, node, "properties")
        try:
            with open(props, "r") as fh:
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


def hip_supported_here(topology_root=KFD_TOPOLOGY_NODES):
    """Whether the HIP build has device code for this machine's GPU.

    True / False / None, where None means the architecture could not be read.

    Being an AMD part is not the same as being a part this build can run on.
    The build carries device code for HIP_GPU_TARGETS and nothing else, and on
    an AMD GPU outside that list llama-server SEGFAULTS at model load instead of
    refusing cleanly — so offering the HIP engine on the strength of the vendor
    id alone offers a download that replaces a working Vulkan setup with a
    crash. Measured on a gfx90c APU.
    """
    detected = detect_amd_gfx_targets(topology_root)
    if not detected:
        return None
    return bool(detected & set(HIP_GPU_TARGETS))


def upgrade_engine_for(vendor):
    """The mirror-only engine build this vendor has, or None.

    The vendor's row in the preference table minus the engine that already
    ships. NVIDIA has CUDA, AMD has HIP/ROCm; Intel and unknown hardware have
    nothing to add, and get None.
    """
    for engine in _ENGINE_PREFERENCE.get(vendor or "", _DEFAULT_PREFERENCE):
        if engine != SHIPPED_ENGINE:
            return engine
    return None


def upgrade_outranks_shipped(vendor):
    """Whether the vendor's added engine is preferred OVER the shipped one.

    Read straight off the ratified table, because that table is what decides
    which engine actually serves once both are present. On AMD, HIP is listed
    ahead of Vulkan, so adding it changes which engine serves. On NVIDIA,
    Vulkan is listed ahead of CUDA — adding CUDA installs it and leaves
    Vulkan serving — and whatever makes the offer must say so rather than let
    a user install several gigabytes expecting a speed-up the project
    measured as a slow-down.
    """
    row = _ENGINE_PREFERENCE.get(vendor or "", _DEFAULT_PREFERENCE)
    upgrade = upgrade_engine_for(vendor)
    if upgrade is None or SHIPPED_ENGINE not in row or upgrade not in row:
        return False
    return row.index(upgrade) < row.index(SHIPPED_ENGINE)


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------

# Where the record lands on the installed system, and the version stamp a
# reader checks before trusting the keys. The path is under /etc because it
# describes the machine the system was installed onto — the same class of
# fact as /etc/vconsole.conf — and because a reader on the first boot must be
# able to find it without knowing which user is logged in.
DETECTION_RECORD_PATH = "etc/intergen/gpu-detection.json"
# 2 (2026-08-06): added gfx_targets and upgrade_engine_supported. Additive only
# — every key version 1 carried is still present with the same meaning, and the
# one reader takes every key through .get(), so a version-1 record on an
# already-installed machine still reads correctly.
DETECTION_RECORD_VERSION = 2


def detection_record(pci_vendors=None):
    """The facts the first-boot offer needs, as a plain dict.

    Every value here is derived at install time from the machine's own
    hardware and the ratified engine table. Nothing about a user's CHOICE is
    recorded, because no choice is made during the install any more: this
    says what the machine is, and the first-boot offer decides what to say
    about it.

    Keys:
      version               schema version of this record
      vendor                "nvidia" / "amd" / "intel", or None when the
                            display controller could not be identified
      pci_vendors           the raw ids the probe returned, sorted — kept so
                            a reader can tell "nothing detected" (empty)
                            apart from "detected, but no vendor we act on"
      shipped_engine        the engine that is installed and serving
      upgrade_engine        the mirror-only engine this vendor can add, or
                            None when there is nothing to add
      upgrade_outranks_shipped
                            whether adding it changes which engine serves
      gfx_targets           the AMD architectures the kernel reported, sorted;
                            empty on non-AMD hardware and on any machine whose
                            topology could not be read
      upgrade_engine_supported
                            True when the added engine has device code for this
                            machine, False when it measurably does not, None
                            when it could not be determined. Always None for
                            engines where the question does not arise.
    """
    if pci_vendors is None:
        pci_vendors = detect_display_pci_vendors()
    vendor = detect_gpu_vendor(pci_vendors)
    upgrade = upgrade_engine_for(vendor)
    # Only HIP is architecture-gated. CUDA runs on every NVIDIA part the
    # proprietary driver supports, so there is no equivalent list to check and
    # inventing one would be a fact this code does not have.
    supported = hip_supported_here() if upgrade == "hip" else None
    return {
        "version": DETECTION_RECORD_VERSION,
        "vendor": vendor,
        "pci_vendors": sorted(pci_vendors),
        "shipped_engine": SHIPPED_ENGINE,
        "upgrade_engine": upgrade,
        "upgrade_outranks_shipped": upgrade_outranks_shipped(vendor),
        "gfx_targets": sorted(detect_amd_gfx_targets()),
        "upgrade_engine_supported": supported,
    }


def write_detection_record(target, pci_vendors=None):
    """Write the record onto the installed system. Returns its path.

    Failure to write is logged and swallowed. A machine whose record could
    not be written still installs correctly and still boots to a working
    desktop with the open source drivers and the Vulkan engine — the user
    simply is not offered the vendor upgrade, which is the same outcome as
    hardware with no upgrade path. Taking the install down over a hint file
    would trade a working system for a missing offer.
    """
    path = os.path.join(str(target), DETECTION_RECORD_PATH)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(detection_record(pci_vendors), fh, indent=2, sort_keys=True)
            fh.write("\n")
    except OSError as exc:
        LOG.warning("gpu-detection: could not write %s (%s); the first-boot "
                    "offer will be skipped on this machine", path, exc)
        return None
    return path
