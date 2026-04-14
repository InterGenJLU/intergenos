"""Hardware detection and tier assignment for InterGen.

Probes system hardware (RAM, GPU) and assigns an LLM tier that determines
which model InterGen loads. Works entirely from /proc and /sys — no
external tools required (lspci used opportunistically if available).

Tier table:
  Tier 1: <8 GB RAM, no/integrated GPU → Qwen3.5-2B Q4_K_M (~1.5 GB)
  Tier 2: 8-15 GB RAM, any GPU          → Qwen3.5-9B Q4_K_M (~5.5 GB)
  Tier 3: 16 GB+ RAM, discrete GPU      → Qwen3.5-35B-A3B MoE Q4_K_M (~21 GB)
"""

from __future__ import annotations

import logging
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
}

# Model recommendations per tier
TIER_MODELS = {
    HardwareTierLevel.TIER_1: {
        "name": "Qwen3.5-2B",
        "quant": "Q4_K_M",
        "size_gb": 1.5,
    },
    HardwareTierLevel.TIER_2: {
        "name": "Qwen3.5-9B",
        "quant": "Q4_K_M",
        "size_gb": 5.5,
    },
    HardwareTierLevel.TIER_3: {
        "name": "Qwen3.5-35B-A3B",
        "quant": "Q4_K_M",
        "size_gb": 21.0,
    },
}


class HardwareDetector(HardwareDetectorInterface):
    """Detects system hardware and assigns an LLM tier."""

    def __init__(self) -> None:
        self._cached: HardwareTier | None = None

    def detect(self) -> HardwareTier:
        """Probe hardware and return tier assignment."""
        ram_gb = self._detect_ram()
        gpu_vendor, gpu_model, gpu_vram = self._detect_gpu()
        tier = self._assign_tier(ram_gb, gpu_vendor)
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
        """Detect GPU vendor and model.

        Strategy:
          1. Walk /sys/class/drm/card*/device/vendor for PCI vendor IDs
          2. If lspci is available, use it for a human-readable model name
          3. Fall back to PCI device ID if lspci isn't installed

        Returns:
            (vendor_name, model_name, vram_mb) — any may be None.
        """
        vendor_name = None
        model_name = None
        vram_mb = None

        # Step 1: sysfs vendor detection
        drm_path = Path("/sys/class/drm")
        if drm_path.exists():
            for card_dir in sorted(drm_path.glob("card[0-9]*")):
                vendor_file = card_dir / "device" / "vendor"
                if not vendor_file.exists():
                    continue
                try:
                    vendor_id = vendor_file.read_text().strip()
                    vendor_name = GPU_VENDORS.get(vendor_id)
                    if vendor_name is None:
                        vendor_name = f"unknown ({vendor_id})"

                    # Read PCI device ID for model identification
                    device_file = card_dir / "device" / "device"
                    if device_file.exists():
                        device_id = device_file.read_text().strip()
                        model_name = f"{vendor_name} [{device_id}]"

                    # Check for NVIDIA VRAM via sysfs
                    mem_file = card_dir / "device" / "mem_info_vram_total"
                    if mem_file.exists():
                        vram_bytes = int(mem_file.read_text().strip())
                        vram_mb = vram_bytes // (1024 * 1024)

                    # Prefer discrete GPUs — if we found nvidia or amd, stop
                    if vendor_name in ("nvidia", "amd"):
                        break
                except (OSError, ValueError) as e:
                    log.warning("Error reading GPU info from %s: %s", card_dir, e)
                    continue

        # Step 2: try lspci for a better model name
        lspci_model = self._try_lspci()
        if lspci_model:
            model_name = lspci_model

        return vendor_name, model_name, vram_mb

    def _try_lspci(self) -> str | None:
        """Try to get GPU model name from lspci. Returns None if unavailable."""
        try:
            result = subprocess.run(
                ["lspci"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return None
            for line in result.stdout.splitlines():
                low = line.lower()
                if "vga" in low or "3d" in low or "display" in low:
                    # Format: "00:02.0 VGA compatible controller: Intel..."
                    parts = line.split(": ", 1)
                    if len(parts) == 2:
                        return parts[1].strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return None

    def _assign_tier(self, ram_gb: float, gpu_vendor: str | None) -> HardwareTierLevel:
        """Assign hardware tier based on RAM and GPU.

        Tier 1: <8 GB RAM or no GPU info
        Tier 2: 8-15 GB RAM
        Tier 3: 16 GB+ RAM with discrete GPU (nvidia/amd)

        Note: 16GB+ with only integrated Intel GPU stays Tier 2 because
        Intel iGPUs can't offload LLM layers effectively. The model would
        run CPU-only anyway, and 21GB model on 16GB RAM leaves no headroom.
        """
        if ram_gb < 8:
            return HardwareTierLevel.TIER_1

        has_discrete = gpu_vendor in ("nvidia", "amd")

        if ram_gb >= 16 and has_discrete:
            return HardwareTierLevel.TIER_3

        return HardwareTierLevel.TIER_2
