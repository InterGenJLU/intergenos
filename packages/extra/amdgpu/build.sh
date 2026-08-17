#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# amdgpu meta-package — no source, no build.
#
# Installs:
#   /usr/share/doc/amdgpu/README         — user documentation
#   /usr/share/doc/amdgpu/LICENSE.amdgpu — symlink to the firmware license
#                                          file already shipped by
#                                          core/linux-firmware. Symlink
#                                          ensures the proprietary-blob
#                                          license is discoverable from a
#                                          conventional location without
#                                          requiring users to dig into
#                                          /usr/lib/firmware/.
#
# Security-only-alignment filter notes (per the security-filter research dossier):
#   - No SUID binaries installed.
#   - No daemons started.
#   - No kernel modules loaded.
#   - Open-source userland only (the proprietary-firmware blobs ship via
#     core/linux-firmware under AMD's redistribution license; that
#     surface is gated by Secure Boot + dm-verity + module signing in
#     the kernel — not by this meta-package).
#   - No /etc/modprobe.d drops, no kernel cmdline edits, no udev rules:
#     defaults are correct on every supported AMD GPU since GCN2.
#
# Cross-distro comparison: no other major distro ships a meta-package
# by this exact name. Closest analog is Debian's
# `firmware-amd-graphics` Recommends pulling in `va-driver-all` +
# `vulkan-tools` + `radeontop`. We provide a Tier-2 mirror-only
# meta-package because our iso_include:false posture means users have
# to opt in; surfacing a single install name is user-control
# transparency.

configure() {
    set -e
    : # no-op (meta-package, no source code)
}

build() {
    set -e
    : # no-op (meta-package, no source code)
}

do_install() {
    set -e
    install -dm755 "${DESTDIR}/usr/share/doc/amdgpu"
    cat > "${DESTDIR}/usr/share/doc/amdgpu/README" <<'EOF'
amdgpu meta-package — InterGenOS
=================================

This package is a convenience meta-package for users of AMD Radeon
graphics hardware (GCN1+ / 2012+).

The kernel driver (amdgpu.ko) and firmware blobs are already part of
the base InterGenOS install — they ship in linux-kernel and
linux-firmware respectively, and a stock InterGenOS install will boot
on any AMD Radeon GPU back to GCN1 (Tahiti, 2012).

Installing this meta-package adds the following user-facing tools:

  vainfo         — VAAPI introspection
                   (from libva-utils; verifies the radeonsi VAAPI driver)

  vulkaninfo     — Vulkan introspection
  vkcube         — Vulkan rendering test
                   (from vulkan-tools; verifies the Mesa RADV driver)

  radeontop      — Live GPU activity stats (htop-style)
  amdgpu_top     — Live GPU dashboard (TUI; richer than radeontop)

  libvdpau-va-gl — VDPAU-to-VAAPI translation layer
                   (Mesa 25.3.0 removed native VDPAU from radeonsi.
                   This layer keeps legacy VDPAU applications working
                   by translating their calls to VAAPI under the hood.
                   Activate by setting VDPAU_DRIVER=va_gl in the
                   environment of the legacy application.)

NOT included (separate concerns):

  - ROCm / HIP compute stack: the compute tier ships rocm-runtime,
    hip-runtime-amd, rocblas, etc. Required for GPGPU workloads
    (PyTorch, llama.cpp HIP, Blender HIP rendering).

  - xf86-video-amdgpu: X11 DDX driver. InterGenOS is Wayland-only on
    GNOME 49; legacy X11 clients run under XWayland, which uses the
    modesetting driver path.

  - Overclocking GUI tools (corectrl, LACT): user-installable
    independently if desired.

Mesa OpenCL via rusticl is enabled in the Mesa build that ships in the
ISO. To use it, set:

  export RUSTICL_ENABLE=radeonsi

then run any OpenCL workload. Tools like clinfo will then list a
"rusticl" platform alongside any other OpenCL platforms present.

Firmware license:
  The AMDGPU firmware blobs at /usr/lib/firmware/amdgpu/ are proprietary
  AMD redistributable binaries — see /usr/share/doc/amdgpu/LICENSE.amdgpu
  for the full license text. This is the only proprietary component in
  the otherwise-open AMDGPU stack. Every Linux distribution that supports
  AMD GPUs ships these blobs under the same license. Integrity of the
  firmware blobs is verified at boot by dm-verity over the squashfs that
  contains /usr/lib/firmware/, and the kernel module that loads them is
  signed by the InterGenOS ephemeral module-signing key.

Runtime knobs:
  /etc/modprobe.d/amdgpu.conf — not shipped by default. Drop your own
  if you need workarounds (e.g. amdgpu.abmlevel=0 to disable Panel
  Power Savings on laptops with washed-out colors).

  /etc/default/grub GRUB_CMDLINE_LINUX — not modified by default.
  Add amdgpu.* parameters here if your hardware needs them.

  See kernel docs at https://docs.kernel.org/gpu/amdgpu/ for the full
  list of module parameters.

Documentation:
  Arch wiki: https://wiki.archlinux.org/title/AMDGPU
  Kernel:    https://docs.kernel.org/gpu/amdgpu/
  Mesa:      https://docs.mesa3d.org/
EOF
    # Symlink to the firmware license so users discover it from the
    # conventional doc location. The target file ships via
    # core/linux-firmware at /usr/lib/firmware/LICENSE.amdgpu.
    ln -sf /usr/lib/firmware/LICENSE.amdgpu \
        "${DESTDIR}/usr/share/doc/amdgpu/LICENSE.amdgpu"
}
