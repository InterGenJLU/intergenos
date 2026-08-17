#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# vulkan-tools 1.4.341.0 — Khronos Vulkan utility programs
#
# Build profile: cmake out-of-source. Standard upstream CMakeLists.
#
# Configure flags chosen:
#   -DCMAKE_BUILD_TYPE=Release   optimized; no debug symbols shipped
#   -DCMAKE_INSTALL_PREFIX=/usr  standard distro layout
#   -DBUILD_ICD=OFF              skip the mock-ICD test driver; we use
#                                Mesa's RADV/ANV from desktop/mesa
#   -DBUILD_VULKANINFO=ON        explicit (default ON); the load-bearing
#                                introspection tool
#   -DBUILD_CUBE=ON              explicit (default ON); rendering sanity
#                                check tool (vkcube + vkcubepp)
#   -DBUILD_WSI_WAYLAND_SUPPORT=ON  build the Wayland WSI back-end so
#                                vkcube can run on Wayland (default ON
#                                on Linux but explicit for clarity)
#   -DBUILD_WSI_XLIB_SUPPORT=ON  XWayland fallback path
#   -DBUILD_WSI_XCB_SUPPORT=ON   X11/XCB native path
#   -DBUILD_TESTS=OFF            skip test executables; environment-bound
#                                (need real Vulkan ICD + GPU)
#   -DBUILD_WERROR=OFF           do not promote warnings to errors;
#                                cross-distro convention for downstream
#                                builds
#
# Cross-distro flag comparison:
#   Arch:    -DBUILD_ICD=OFF -DBUILD_WSI_*=ON -DBUILD_TESTS=OFF
#   Fedora:  -DBUILD_ICD=OFF -DBUILD_WSI_*=ON -DBUILD_TESTS=OFF
#   Debian:  -DBUILD_ICD=OFF -DBUILD_WSI_*=ON (tests separate package)
# We align exactly.
#
# Security-only-alignment filter: pure command-line utilities, no SUID, no daemon,
# no setuid/network capabilities. Vulkan ICD enumeration reads
# /usr/share/vulkan/icd.d/ files but never executes them; they're JSON
# manifests pointing at .so files the loader then dlopen()s. Same
# trust-the-library-loader model as every Vulkan-using app on the
# system.

configure() {
    set -e
    cmake -B build                                                  \
          -DCMAKE_BUILD_TYPE=Release                                \
          -DCMAKE_INSTALL_PREFIX=/usr                               \
          -DBUILD_ICD=OFF                                           \
          -DBUILD_VULKANINFO=ON                                     \
          -DBUILD_CUBE=ON                                           \
          -DBUILD_WSI_WAYLAND_SUPPORT=ON                            \
          -DBUILD_WSI_XLIB_SUPPORT=ON                               \
          -DBUILD_WSI_XCB_SUPPORT=ON                                \
          -DBUILD_TESTS=OFF                                         \
          -DBUILD_WERROR=OFF
}

build() {
    set -e
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
