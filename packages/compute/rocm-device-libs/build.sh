#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# rocm-device-libs 7.2.4 — AMD device-side bitcode libraries
# Source: amd/device-libs inside the ROCm llvm-project tarball
#
# Standalone-build path per the pinned tarball's own README
# (amd/device-libs/README.md "BUILDING"): point CMAKE_PREFIX_PATH at a
# built clang/llvm — ours is the rocm-llvm package at /opt/rocm/lib/llvm.
# Installs the amdgcn bitcode under /opt/rocm/amdgcn/bitcode, the default
# location amdclang searches when targeting amdhsa (--rocm-path root).

configure() {
    set -e
    cd amd/device-libs
    mkdir -p build

    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_PREFIX_PATH=/opt/rocm/lib/llvm \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    cd amd/device-libs
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    cd amd/device-libs
    DESTDIR="$DESTDIR" cmake --install build
}
