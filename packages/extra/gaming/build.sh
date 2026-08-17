#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# gaming meta-package — no source, no build (amdgpu-meta precedent).
#
# Installs:
#   /usr/share/doc/gaming/README — user documentation, carrying the
#   RT-5 MEASURED wording for the NVK (NVIDIA) rung: on-metal (RTX 3070
#   Ti, 2026-07-08) NVK's 32-bit ICD does not reach the dGPU, so 32-bit
#   titles render on the iGPU — see the RT-5 note in package.yml.
#
# Security-only-alignment filter notes:
#   - No SUID binaries, no daemons, no kernel modules, no udev rules,
#     no config drops. A README is the only payload.
#   - Every proprietary payload in the set (Steam client, GE-Proton
#     runtime, Wine's gecko/mono MSIs) arrives through its own
#     sha-pinned / signature-verified download helper — this meta only
#     names the set.

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
    install -dm755 "${DESTDIR}/usr/share/doc/gaming"
    cat > "${DESTDIR}/usr/share/doc/gaming/README" <<'EOF'
gaming meta-package — InterGenOS
=================================

This package is a convenience meta-package that installs the complete
InterGenOS gaming stack in one step:

  steam          — the Steam client (Valve's official signed payload,
                   fetched and verified by the igos-install-steam
                   helper; the /usr/bin/steam launcher fail-closed
                   asserts the 32-bit runtime before starting)

  wine           — Windows compatibility layer (from source, new-WoW64)
  wine-gecko     — Wine's bundled browser engine (Wine's own pinned MSIs)
  wine-mono      — Wine's bundled .NET runtime (Wine's own pinned MSI)

  dxvk           — Direct3D 8/9/10/11 over Vulkan (built from source)
  vkd3d-proton   — Direct3D 12 over Vulkan (built from source)

  ge-proton      — GloriousEggroll's Proton build for Steam Play
                   (sha-verified download helper; select it per-title
                   in Steam's compatibility settings)

  lib32-*        — the complete 32-bit runtime (40 packages: glibc,
                   Mesa, the Vulkan loader, the X11/Wayland client
                   stack, the audio stack, NSS, and their closures),
                   which 32-bit Windows titles and the Steam client
                   itself require.

GPU support status:

  AMD (RADV) and Intel (ANV) Mesa Vulkan drivers are the primary
  target rails — the same drivers the 64-bit desktop already uses,
  with their 32-bit twins in lib32-mesa / lib32-vulkan-loader.

  NVIDIA (NVK, Mesa's open driver) — MEASURED on real hardware
  (RTX 3070 Ti, Ampere, 2026-07-08): the 64-bit stack resolves to the
  NVIDIA dGPU inside the container, but NVK's 32-bit ICD does NOT
  enumerate the dGPU at all — 32-bit Vulkan sees only the integrated
  GPU. So 32-bit titles are PLAYABLE, but they render on the iGPU,
  never on the NVIDIA dGPU. Putting 32-bit titles on the dGPU needs a
  proprietary 32-bit NVIDIA Vulkan ICD (a lib32-nvidia-utils
  equivalent), which InterGenOS does not ship today.

NOT included (separate concerns):

  - The mingw-w64 cross toolchain: build-time tooling that compiles
    the DXVK / VKD3D-Proton PE DLLs from source. It lives in the
    build system, not on your machine.

  - Valve Proton: the Steam client downloads and manages its own
    official Proton builds. GE-Proton (above) is the community
    variant InterGenOS ships a verified helper for.

  - 32-bit ncurses: measured absent from both Valve's dependency set
    and Arch's steam package — deliberately not shipped.

Verification posture (why you can trust this stack):

  Every from-source package above builds from sha256-pinned upstream
  sources inside the InterGenOS build chroot. Every proprietary
  payload arrives through a download helper that refuses to install
  unless its vendor-signature / pinned-digest chain verifies — a
  failed verification halts loudly; nothing installs silently.
EOF
}
