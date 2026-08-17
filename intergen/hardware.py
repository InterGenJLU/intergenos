# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Hardware detection and tier assignment for InterGen.

Probes system hardware (RAM, GPU) and assigns an LLM tier that determines
which model InterGen loads. Works entirely from /proc and /sys — no
external tools required (lspci used opportunistically if available).

Tier table — GPU + VRAM ONLY, evaluated top-down (design decision,
2026-07-24). System RAM is NEVER an input to tier assignment: a box without a
discrete GPU serves the 2B for latency regardless of RAM (the 9B on CPU is
~50s/query — unusable), so there is no decision RAM could inform. Unknown
capability always fails DOWN, never up.

  no discrete/external GPU          → Tier 1: InternVL3.5-2B Q4_K_M (~1.3 GB)
  discrete GPU, VRAM >= ~22 GB      → Tier 3: Qwen3.5-35B-A3B MoE Q4_K_M (~22 GB)
  discrete GPU, VRAM >= ~7 GB       → Tier 2: Qwen3.5-9B Q4_K_M (~5.6 GB)
  discrete GPU, smaller or unknown  → Tier 1 (fail down)
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

from intergen.interfaces.hardware import HardwareDetectorInterface
from intergen.interfaces.types import HardwareTier, HardwareTierLevel

log = logging.getLogger(__name__)

# PCI vendor IDs for GPU detection
GPU_VENDORS = {
    "0x10de": "nvidia",
    "0x1002": "amd",
    "0x8086": "intel",
    # Virtual GPUs — named so a VM readout reads e.g. "vmware" rather than
    # "unknown (0x15ad)". These are NOT discrete-capable: they fall through
    # _is_discrete_capable to CPU-only treatment (they export no dedicated VRAM),
    # so tier/model selection is UNCHANGED — this only improves the human-readable
    # vendor label for the large share of evaluators who try the distro in a VM.
    # A software-rendered virtual GPU (llvmpipe) carries no VRAM and so correctly
    # lands CPU-only regardless of the label.
    "0x15ad": "vmware",   # VMware SVGA II — also VirtualBox VMSVGA
    "0x1af4": "virtio",   # virtio-gpu — QEMU/KVM, virt-manager, libvirt
    "0x1234": "qemu",     # QEMU stdvga / bochs-display
}

# A discrete GPU is distinguished from an integrated APU/iGPU by dedicated VRAM.
# Integrated parts (AMD APUs, Intel iGPUs) carve a small buffer out of system
# RAM — typically <=2 GB — while even entry-level discrete cards ship 4 GB+.
# 3 GB cleanly separates the two and is the gate for picking the larger,
# GPU-accelerated model. Vendor ID alone is NOT sufficient: an integrated AMD
# APU (e.g. the A12, PCI vendor 0x1002) reports the same vendor as a discrete
# Radeon, so a vendor-only test wrongly loaded the 9B model that then ran
# CPU-only at ~50s/query.
DISCRETE_VRAM_THRESHOLD_MB = 3072

# Per-tier VRAM fit gates (the ONLY tier inputs beyond discrete-GPU presence;
# Decided design 2026-07-24 — the prior RAM-threshold table and the
# RAM-based Tier-3 "expert-offload" leg are REMOVED: RAM is never evaluated,
# and running the 35B with experts in system RAM is an explicit operator
# decision (config/override), never something detection infers).
#   - Tier 3 RESIDENT: the card holds the ~22 GB model + KV/vision buffers in
#     VRAM. 22 GB clears it with working headroom (a 24/32 GB card qualifies;
#     a 20 GB card deliberately does not).
#   - Tier 2: the card holds the 5.6 GB 9B + its ~1 GB vision projector +
#     KV/compute buffers (~5.8 GB observed at load). 7 GiB is the floor with
#     headroom — an 8 GB card qualifies, a 6 GB card does not.
# Sizes are grounded in the catalog entries + observed load-time buffers.
TIER3_RESIDENT_VRAM_MB = 22000
TIER2_VRAM_MB = 7168

# ggml's ``--list-devices`` line shape — the SAME pattern intergen.serving_device
# parses (minus the name group), kept identical on purpose so both readers agree
# about what a device line is: "  Vulkan0: NAME (TOTAL MiB, FREE MiB free)",
# optionally followed by the " [PCI domain:bus:device.function]" suffix the
# in-tree list-devices-pci-id.patch adds in every engine recipe. The tail is
# OPTIONAL by design — unpatched builds and id-less devices still parse. The
# two regexes must change in lockstep.
_LIST_DEVICES_RE = re.compile(
    r"^\s+\w+?\d+:\s+(?P<desc>.+?)\s+\((?P<total>\d+)\s*MiB,"
    r"\s*\d+\s*MiB free\)"
    r"(?:\s+\[PCI\s+[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}"
    r"\.[0-7]\])?\s*$", re.MULTILINE)

# Model recommendations per tier. CPU-only boxes are Tier 1 BY CONSTRUCTION
# (no discrete GPU → Tier 1), which is the latency rule made structural:
# 9B on CPU-only = 50s/query (prompt caching only 27% effective) vs
# 2B on CPU-only = 17s/query, both with --reasoning off.
TIER_MODELS = {
    HardwareTierLevel.TIER_1: {
        "name": "InternVL3.5-2B",
        "quant": "Q4_K_M",
        "size_gb": 1.3,
    },
    HardwareTierLevel.TIER_2: {
        "name": "Qwen3.5-9B",
        "quant": "Q4_K_M",
        "size_gb": 5.6,
    },
    HardwareTierLevel.TIER_3: {
        "name": "Qwen3.5-35B-A3B",
        "quant": "Q4_K_M",
        "size_gb": 22.0,
    },
}


def open_driver_vram_mb(name_contains: str,
                        list_output: str | None = None) -> int | None:
    """Largest video-memory figure THE SERVING STACK ITSELF reports for a
    device whose description contains ``name_contains`` (case-insensitive).

    Why this exists: the open-source NVIDIA kernel driver (nouveau) exports
    no ``mem_info_vram_total``, so every sysfs/driver reader returns None and the
    card's size reads as unknown — which is what made setup tell a user it
    could not determine the card's memory. It CAN be determined. Mesa's NVK
    driver reports the card's heap through the very enumeration
    ``intergen.serving_device`` already parses. Measured on a GTX 1650
    Mobile (2026-08-03)::

        Vulkan0: NVIDIA GeForce GTX 1650 (NVK TU117) (4352 MiB, 3916 MiB free)

    the same card whose kernel log line read ``VRAM: 4096 MiB``.

    ⚠ A reported heap is NOT proof of a dedicated card. On that same laptop
    the Intel integrated adapter reports 5704 MiB — LARGER than the real
    4 GB card — because an integrated part borrows system memory. So the
    caller must already know from the PCI vendor id that it is asking about
    a dedicated card, and ``name_contains`` scopes the match to that
    vendor's own device line.

    ⚠ Deliberately NOT wired into HardwareDetector._enumerate_gpus. Feeding this
    figure into detection would change the DETECTED TIER on an open-driver
    box — an 8 GB card would read as Tier 2 — and the daemon would then try
    to serve a 9B through a driver whose offload we have not proven (the
    .200 measured no usable offload on nouveau until the proprietary driver
    went on). The figure is used to tell the user the TRUTH about their
    card; it does not unlock a rung.

    Returns None when the serving binary is absent or unreadable, which
    leaves every caller exactly where it was before this reader existed.
    """
    if list_output is None:
        server = shutil.which("llama-server") or "/usr/bin/llama-server"
        try:
            proc = subprocess.run([server, "--list-devices"],
                                  capture_output=True, text=True, timeout=30)
            list_output = (proc.stdout or "") + (proc.stderr or "")
        except (OSError, subprocess.TimeoutExpired):
            return None
    needle = name_contains.lower()
    best: int | None = None
    for m in _LIST_DEVICES_RE.finditer(list_output or ""):
        if needle not in m.group("desc").lower():
            continue
        total = int(m.group("total"))
        best = total if best is None else max(best, total)
    return best


class HardwareDetector(HardwareDetectorInterface):
    """Detects system hardware and assigns an LLM tier."""

    def __init__(self) -> None:
        self._cached: HardwareTier | None = None

    def detect(self) -> HardwareTier:
        """Probe hardware and return tier assignment."""
        ram_gb = self._detect_ram()
        gpu_vendor, gpu_model, gpu_vram = self._detect_gpu()
        is_discrete = self._is_discrete_capable(gpu_vendor, gpu_vram)
        tier = self._assign_tier(is_discrete, gpu_vram_mb=gpu_vram)
        model_info = TIER_MODELS[tier]

        result = HardwareTier(
            ram_gb=ram_gb,
            gpu_vendor=gpu_vendor,
            gpu_model=gpu_model,
            gpu_vram_mb=gpu_vram,
            tier=tier,
            recommended_model=model_info["name"],
            recommended_quant=model_info["quant"],
            estimated_model_size_gb=model_info["size_gb"],
        )
        self._cached = result
        log.info(
            "Hardware detected: %.1f GB RAM, GPU=%s (%s), Tier %d → %s %s",
            result.ram_gb,
            result.gpu_vendor or "none",
            result.gpu_model or "none",
            result.tier.value,
            result.recommended_model,
            result.recommended_quant,
        )
        return result

    def get_tier(self) -> HardwareTier:
        """Return cached tier (calls detect() on first access)."""
        if self._cached is None:
            self.detect()
        return self._cached

    def _detect_ram(self) -> float:
        """Read total RAM from /proc/meminfo in GB."""
        try:
            meminfo = Path("/proc/meminfo").read_text()
            for line in meminfo.splitlines():
                if line.startswith("MemTotal:"):
                    # Format: "MemTotal:       16059160 kB"
                    kb = int(line.split()[1])
                    return round(kb / 1048576, 1)  # kB → GB
        except (OSError, ValueError, IndexError) as e:
            log.error("Failed to read /proc/meminfo: %s", e)
        return 0.0

    def _detect_gpu(self) -> tuple[str | None, str | None, int | None]:
        """Detect the most capable GPU's vendor, model, and VRAM.

        Strategy:
          1. Enumerate every /sys/class/drm/card*/device for PCI vendor + VRAM
          2. Pick the most capable card (discrete-capable first, then by VRAM)
             so a hybrid APU+dGPU laptop reports its discrete part, not the iGPU
          3. If lspci is available, use it for a human-readable model name

        Returns:
            (vendor_name, model_name, vram_mb) — any may be None.
        """
        gpus = self._enumerate_gpus()

        vendor_name = None
        model_name = None
        vram_mb = None
        if gpus:
            # Rank: discrete-capable cards beat integrated; ties broken by VRAM.
            vendor_name, model_name, vram_mb = max(
                gpus,
                key=lambda g: (self._is_discrete_capable(g[0], g[2]), g[2] or 0),
            )

        # Prefer lspci for a human-readable model name when available — but
        # ONLY a line naming the SELECTED card. Vendor matching alone is not
        # enough: on a hybrid iGPU+dGPU laptop the first display line is the
        # integrated card (PI-Z13, "nvidia" labeled "Intel … Iris Xe"), and on
        # a dual-card SAME-vendor box the first vendor line can be the wrong
        # card (the dual-Radeon dev PC: the selected 20 GB card was labeled
        # with the 8 GB card's name). Thread the selected card's PCI device id
        # so the exact card's line wins when lspci -nn provides ids.
        selected_device_id = None
        if model_name:
            lb, rb = model_name.rfind("[0x"), model_name.rfind("]")
            if lb != -1 and rb > lb:
                selected_device_id = model_name[lb + 1:rb]
        lspci_model = self._try_lspci(vendor_name, selected_device_id)
        if lspci_model:
            model_name = lspci_model

        return vendor_name, model_name, vram_mb

    def _enumerate_gpus(self) -> list[tuple[str | None, str | None, int | None]]:
        """Read every DRM card's (vendor_name, model_name, vram_mb) from sysfs."""
        gpus: list[tuple[str | None, str | None, int | None]] = []
        drm_path = Path("/sys/class/drm")
        if not drm_path.exists():
            return gpus

        for card_dir in sorted(drm_path.glob("card[0-9]*")):
            vendor_file = card_dir / "device" / "vendor"
            if not vendor_file.exists():
                continue
            try:
                vendor_id = vendor_file.read_text().strip()
                vendor_name = GPU_VENDORS.get(vendor_id, f"unknown ({vendor_id})")

                # Read PCI device ID for model identification
                model_name = None
                device_file = card_dir / "device" / "device"
                if device_file.exists():
                    device_id = device_file.read_text().strip()
                    model_name = f"{vendor_name} [{device_id}]"

                # Dedicated VRAM via sysfs (amdgpu exports this; nouveau does
                # NOT — measured 2026-07-31 on a live nouveau-bound NVIDIA
                # card, no mem_info_vram_total — and Intel iGPUs and APUs
                # without a carve-out report nothing).
                vram_mb = None
                mem_file = card_dir / "device" / "mem_info_vram_total"
                if mem_file.exists():
                    vram_bytes = int(mem_file.read_text().strip())
                    vram_mb = vram_bytes // (1024 * 1024)

                # The NVIDIA proprietary driver does NOT export
                # mem_info_vram_total — without a fallback reader, every such
                # card reads VRAM-unknown and (correctly, per the fail-down
                # rule) lands on the Tier-1 floor. Read the driver's own
                # procfs/NVML surfaces instead so the common case is KNOWN.
                if vram_mb is None and vendor_name == "nvidia":
                    vram_mb = self._nvidia_vram_mb()

                gpus.append((vendor_name, model_name, vram_mb))
            except (OSError, ValueError) as e:
                log.warning("Error reading GPU info from %s: %s", card_dir, e)
                continue

        return gpus

    def _is_discrete_capable(
        self, gpu_vendor: str | None, gpu_vram_mb: int | None
    ) -> bool:
        """Whether the GPU can meaningfully accelerate LLM offload.

        Replaces the old vendor-only test (``vendor in {nvidia, amd}``), which
        misclassified integrated AMD APUs (e.g. the A12, PCI vendor 0x1002) as
        discrete and picked the 9B model — which then ran CPU-only at ~50s/query.

        Rule — a dedicated vendor ID is NOT by itself proof the card can offload
        the tier's model; VRAM-gate against the threshold whenever VRAM is known:
          - AMD / Intel: APUs and iGPUs share system RAM and report little/no
            dedicated VRAM. Discrete only when dedicated VRAM >= threshold;
            unknown VRAM => NOT discrete (an amd/intel part with no VRAM export
            is almost always an integrated iGPU/APU — safe-fail to the floor).
          - NVIDIA: no nvidia iGPU exists on x86, so any nvidia part is a
            dedicated card. VRAM-gate it when the driver exports VRAM — a relic
            low-VRAM card (e.g. a 1 GB GT 710) cannot hold the 5.6 GB 9B (let
            alone the 21 GB 35B), so it must select the 2B floor, not the 9B.
            The prior code returned True for ANY nvidia vendor ID, so that relic
            wrongly selected the 9B/35B; this closes that gate. BUT the nvidia
            proprietary driver frequently does NOT export mem_info_vram_total,
            so UNKNOWN nvidia VRAM must not flatly floor a real dGPU — that would
            pick an integrated APU over a discrete nvidia (a worse bug, guarded
            by test_most_capable_gpu_wins_selection). Unknown nvidia VRAM =>
            tentatively capable; the launch-time offload checked-gate
            (llama_manager, StartFailure.OFFLOAD_FAILED) is the runtime backstop
            that falls to the 2B floor LOUDLY if a card that read as capable does
            not actually offload. (nvidia VRAM readability is validated on the
            in-house RTX 3070 at lane item 5; a known-VRAM path there exercises
            the relic gate directly.)
        """
        if gpu_vendor in ("amd", "intel"):
            return gpu_vram_mb is not None and gpu_vram_mb >= DISCRETE_VRAM_THRESHOLD_MB
        if gpu_vendor == "nvidia":
            # Unknown VRAM: tentatively capable FOR CARD SELECTION ONLY (no
            # nvidia iGPU exists on x86, so this must outrank a real iGPU in
            # _detect_gpu's ranking). Tier assignment is stricter: an unknown
            # VRAM value fails DOWN to the floor in _assign_tier — the
            # _nvidia_vram_mb fallback readers (procfs, nvidia-smi) make that
            # case rare on a working driver. Known VRAM: gate it, so a relic
            # low-VRAM card is never treated as offload-capable.
            if gpu_vram_mb is None:
                return True
            return gpu_vram_mb >= DISCRETE_VRAM_THRESHOLD_MB
        return False

    def _nvidia_vram_mb(self) -> int | None:
        """Best-effort dedicated-VRAM read for the NVIDIA proprietary driver.

        Two sources, in order; the largest value wins when multiple GPUs are
        present (consistent with _detect_gpu's best-card selection):
          1. /proc/driver/nvidia/gpus/*/information — "Video Memory: N MBytes"
          2. nvidia-smi --query-gpu=memory.total (CSV, MiB)
        Returns None when neither source is readable — the caller then treats
        VRAM as unknown and tier assignment fails down.
        """
        best: int | None = None
        try:
            for info in Path("/proc/driver/nvidia/gpus").glob("*/information"):
                for line in info.read_text().splitlines():
                    if "Video Memory" in line:
                        # Format: "Video Memory:    8192 MBytes"
                        digits = [t for t in line.split() if t.isdigit()]
                        if digits:
                            mb = int(digits[0])
                            best = mb if best is None else max(best, mb)
        except (OSError, ValueError):
            pass
        if best is not None:
            return best
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                values = [int(v.strip()) for v in result.stdout.split()
                          if v.strip().isdigit()]
                if values:
                    return max(values)
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            pass
        return None

    def _try_lspci(
        self,
        vendor_name: str | None = None,
        device_id: str | None = None,
    ) -> str | None:
        """Try to get a GPU model name from lspci. Returns None if unavailable.

        Matching precedence for the SELECTED card:
          1. ``device_id`` (e.g. "0x744c") against ``lspci -nn``'s
             ``[vvvv:dddd]`` suffix — the only match that is exact on a
             dual-card same-vendor box (two Radeons: a vendor match returns
             whichever AMD line lspci prints first, which mislabeled the
             selected card on the dual-Radeon dev PC).
          2. ``vendor_name`` substring — still catches the hybrid
             different-vendor case (PI-Z13) when no device id is available.
        No matching line → None, so the caller keeps the sysfs name for the
        selected card instead of a wrong-card string.
        """
        try:
            result = subprocess.run(
                ["lspci", "-nn"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return None
            candidates = []
            for line in result.stdout.splitlines():
                low = line.lower()
                if "vga" in low or "3d" in low or "display" in low:
                    # Format: "03:00.0 VGA compatible controller [0300]:
                    #          … [Radeon RX 7900 XT] [1002:744c] (rev c8)"
                    parts = line.split(": ", 1)
                    if len(parts) == 2:
                        candidates.append(parts[1].strip())

            def _strip_pci_ids(name: str) -> str:
                # Drop the trailing "[vvvv:dddd]" id tag (keep the human name).
                lb = name.rfind(" [")
                if lb != -1 and ":" in name[lb:] and name.rstrip().endswith(
                        (")", "]")):
                    tag = name[lb + 2:]
                    head = tag.split("]", 1)[0]
                    if ":" in head and all(
                            c in "0123456789abcdefABCDEF:" for c in head):
                        return (name[:lb] + tag.split("]", 1)[1]).strip()
                return name

            if device_id:
                want_id = f":{device_id.lower().removeprefix('0x')}]"
                for cand in candidates:
                    if want_id in cand.lower():
                        return _strip_pci_ids(cand)
            if vendor_name:
                want = vendor_name.lower()
                for cand in candidates:
                    if want in cand.lower():
                        return _strip_pci_ids(cand)
                return None
            return _strip_pci_ids(candidates[0]) if candidates else None
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return None

    def _assign_tier(
        self,
        is_discrete: bool,
        gpu_vram_mb: int | None = None,
    ) -> HardwareTierLevel:
        """Assign the hardware tier from GPU capability ONLY, top-down.

        design decision (2026-07-24):
          - System RAM is NEVER an input. A box without a discrete GPU serves
            the Tier-1 2B for latency regardless of RAM, so there is no
            decision RAM could inform; and the prior RAM-based Tier-3
            "expert-offload" leg is removed — running the 35B with experts in
            system RAM is an explicit operator decision (config/override),
            never something detection infers.
          - Unknown capability fails DOWN, never up. A discrete card whose
            VRAM cannot be read (after every reader, including the NVIDIA
            fallback in :meth:`_enumerate_gpus`) lands on the Tier-1 floor —
            a loud, safe under-serve, never a dead-end over-reach.

        Top-down walk for a discrete card with known VRAM:
          >= TIER3_RESIDENT_VRAM_MB → Tier 3 (the 35B resident in VRAM)
          >= TIER2_VRAM_MB          → Tier 2 (the 9B + projector + KV fit)
          smaller                   → Tier 1

        ``is_discrete`` comes from :meth:`_is_discrete_capable` — a VRAM-backed
        capability test, NOT a vendor-ID guess (an integrated APU/iGPU is never
        discrete regardless of vendor).
        """
        if not is_discrete:
            return HardwareTierLevel.TIER_1

        if gpu_vram_mb is None:
            # Unknown VRAM on a discrete-looking card: fail DOWN (the floor),
            # never up. The NVIDIA fallback readers make this case rare.
            return HardwareTierLevel.TIER_1

        if gpu_vram_mb >= TIER3_RESIDENT_VRAM_MB:
            return HardwareTierLevel.TIER_3
        if gpu_vram_mb >= TIER2_VRAM_MB:
            return HardwareTierLevel.TIER_2
        return HardwareTierLevel.TIER_1
