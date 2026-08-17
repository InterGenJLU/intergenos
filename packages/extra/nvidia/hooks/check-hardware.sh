#!/bin/bash
# check-hardware.sh — Turing+ NVIDIA GPU detection
#
# Exit 0 = supported NVIDIA GPU present (Turing / Ampere / Ada / Blackwell).
# Exit 1 = no supported NVIDIA GPU present.
#
# Detection: lspci PCI device IDs under vendor 0x10de (NVIDIA). The
# architectural break point is Turing — NVIDIA's open-gpu-kernel-modules
# only supports Turing-and-newer (compute capability >= 7.5).
#
# PCI device ID ranges (approximate; canonical table lives at
# /usr/src/nvidia-open-${ver}/supported-gpus/supported-gpus.json):
#   Turing:    0x1e00 - 0x1fff  (RTX 20xx, GTX 16xx, T-series)
#   Ampere:    0x2200 - 0x24ff  (RTX 30xx, A-series, GA10x)
#   Ada:       0x2680 - 0x27ff  (RTX 40xx, L-series, AD10x)
#   Blackwell: 0x2800 - 0x2fff  (RTX 50xx, B-series, GB10x/GB20x)
#
# Older NVIDIA GPUs (Pascal GTX 10xx, Maxwell GTX 9xx, Kepler GTX 7xx,
# Fermi GTX 5xx-6xx, older) fall back to the in-kernel nouveau driver
# (CONFIG_DRM_NOUVEAU=m, already shipped in our kernel). nouveau is fine
# for basic acceleration on this older hardware.

set -uo pipefail

if ! command -v lspci >/dev/null 2>&1; then
    echo "[nvidia:check-hardware] WARNING: lspci not on PATH" >&2
    echo "[nvidia:check-hardware]   Cannot check for a GPU without it. Continuing anyway." >&2
    exit 0
fi

# Vendor 10de = NVIDIA. Filter for VGA/3D controller class.
# lspci -n format: "<bus> <class>: <vendor:device> [...]" on most distros.
NV_GPUS=$(lspci -n 2>/dev/null | awk '$2 ~ /^030[02]:?/ && $3 ~ /^10de:/ {sub(/^10de:/, "", $3); print $3}')

# Fallback: simpler grep on the unfiltered output (some lspci versions
# format slightly differently and class doesn't filter cleanly).
if [ -z "$NV_GPUS" ]; then
    NV_GPUS=$(lspci -n 2>/dev/null | awk '$3 ~ /^10de:/ {sub(/^10de:/, "", $3); print $3}')
fi

if [ -z "$NV_GPUS" ]; then
    echo "[nvidia:check-hardware] no NVIDIA GPU detected on PCI bus" >&2
    exit 1
fi

# Walk every detected NVIDIA GPU. Exit 0 if ANY is Turing+.
SUPPORTED=0
for devid in $NV_GPUS; do
    # bash arithmetic on hex
    devid_int=$((16#$devid))
    # Turing's earliest device ID is 0x1e02 (TITAN RTX); we use 0x1e00 as
    # the lower bound to cover any pre-release / engineering-sample IDs
    # that fall in the Turing arch range. Anything >= 0x1e00 in the
    # NVIDIA vendor ID space is Turing-or-newer on current silicon.
    if [ "$devid_int" -ge $((16#1e00)) ]; then
        SUPPORTED=1
        echo "[nvidia:check-hardware] found supported NVIDIA GPU 10de:$devid" >&2
        # Loop continues to log all detected GPUs but the conclusion
        # is already determined.
    fi
done

if [ $SUPPORTED -eq 0 ]; then
    echo "[nvidia:check-hardware] no Turing-or-newer NVIDIA GPU found." >&2
    echo "[nvidia:check-hardware]   Detected NVIDIA GPUs: $NV_GPUS" >&2
    echo "[nvidia:check-hardware]   Pre-Turing NVIDIA GPUs are not supported by NVIDIA's open kernel modules." >&2
    echo "[nvidia:check-hardware]   The nouveau driver (already in your kernel) is the supported path." >&2
    echo "[nvidia:check-hardware]   To verify nouveau is active: lsmod | grep nouveau" >&2
    exit 1
fi

exit 0
