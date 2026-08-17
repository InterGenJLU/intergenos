#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# lib32-vulkan-loader 1.4.341.0 — 32-bit Vulkan ICD loader (GE arc, Wave 2)
# Sibling: packages/desktop/vulkan-loader (same tarball/version — RT-9 +
# LIB32-SOURCE-DRIFT locks; G4 locks all three vulkan packages together).
# Built through THE cmake toolchain-file twin; WSI platforms pinned
# EXPLICITLY (G3 philosophy — auto-detection is the silent-degradation
# class this arc's gates exist to prevent).

source /mnt/intergenos/scripts/lib32-env.sh

configure() {
    set -e
    # Sibling parity: BLFS update_deps depth fix (harmless if unused).
    sed "s/'git', 'clone'/\&, '--depth=1', '-b', self.commit/" -i scripts/update_deps.py

    cmake -B build                                                            \
          -D CMAKE_TOOLCHAIN_FILE=/mnt/intergenos/config/lib32/lib32-cmake-toolchain.cmake \
          -D CMAKE_INSTALL_PREFIX=/usr                                        \
          -D CMAKE_INSTALL_LIBDIR=lib32                                       \
          -D CMAKE_BUILD_TYPE=Release                                         \
          -D BUILD_WSI_XCB_SUPPORT=ON                                         \
          -D BUILD_WSI_XLIB_SUPPORT=ON                                        \
          -D BUILD_WSI_WAYLAND_SUPPORT=ON                                     \
          -D CMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    cmake --build build -j${IGOS_JOBS} --verbose
}

do_install() {
    set -e
    DESTDIR="$PWD/m32root" cmake --install build

    # Allowlist-stage the lib32 tree only (the loader's headers/cmake files
    # are owned by the 64-bit sibling + vulkan-headers).
    lib32_stage_libs "$PWD/m32root"
    lib32_assert_only_lib32
    lib32_env_end
}
