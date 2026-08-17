#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# libglvnd 1.7.0 — GL Vendor-Neutral Dispatch library
# Upstream: https://gitlab.freedesktop.org/glvnd/libglvnd
#
# Pure meson build, no patches needed. The library is small (~5K LOC C)
# and stable — upstream cadence is one release every 18-24 months.
#
# Configure flags rationale:
#   --buildtype=release        — strip debug symbols + optimize -O2
#   -Dx11=enabled              — build libGLX dispatch (NVIDIA libGLX_nvidia
#                                registers here; without x11 dispatch the
#                                vendor JSON files are ineffective)
#   -Degl=true                 — build libEGL dispatch (NVIDIA libEGL_nvidia
#                                registers here for Wayland)
#   -Dglx=enabled              — explicit X-Windows GLX support
#   -Dtls=true                 — thread-local storage for current-context tracking
#                                (every other distro defaults this on; explicit
#                                for reproducibility)
#   -Dasm=enabled              — assembly-optimized dispatch stubs
#                                (faster GL call indirection vs. C fallback;
#                                Arch + Fedora + Debian all enable)
#   -Dgles1=true               — OpenGL ES 1.x dispatch
#                                (legacy ES support; tiny code footprint
#                                and required by some embedded apps)
#   -Dgles2=true               — OpenGL ES 2.x dispatch
#                                (required by every modern GLES app + by
#                                NVIDIA's Vulkan-WSI integration)
#   -Dheaders=true             — install GL/GLES/EGL/GLX headers
#                                (NVIDIA module build needs glcorearb.h)
#
# Not setting:
#   -Dentrypoint-patching       — leave at meson default (auto); upstream
#                                 chooses the right thing per arch
#
# Cross-distro alignment:
#   Arch       PKGBUILD: -D x11=enabled -D egl=true -D glx=enabled
#   Fedora     spec: same flags + glvnd dispatch enabled in mesa
#   Debian     debian/rules: same flags

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..               \
          --prefix=/usr          \
          --libdir=/usr/lib      \
          --buildtype=release    \
          -Dx11=enabled          \
          -Degl=true             \
          -Dglx=enabled          \
          -Dtls=true             \
          -Dasm=enabled          \
          -Dgles1=true           \
          -Dgles2=true           \
          -Dheaders=true
}

build() {
    set -e
    cd build
    ninja
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
}
