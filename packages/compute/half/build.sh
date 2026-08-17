#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2026 InterGenJLU
#
# half 1.12.0 (ROCm packaging, rocm-5.6.0 tag) — single-header install
#
# BUILD_FILE_REORG_BACKWARD_COMPATIBILITY=OFF: skips the deprecated
# top-level half.hpp wrapper; consumers use the canonical
# <half/half.hpp> path (what MIGraphX and MIOpen probe for).

configure() {
    set -e
    mkdir -p build
    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_PREFIX_PATH=/opt/rocm \
        -DBUILD_FILE_REORG_BACKWARD_COMPATIBILITY=OFF \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
