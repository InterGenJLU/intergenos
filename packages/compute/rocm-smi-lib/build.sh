#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# rocm-smi-lib 7.2.4 — ROCm SMI library + rocm-smi CLI
# Source: projects/rocm-smi-lib inside rocm-systems
#
# Host-side C++ (sysfs/KFD readers) — the system compiler builds it; no
# HIP/device code. Tests default OFF and stay OFF.

configure() {
    set -e
    cd projects/rocm-smi-lib
    mkdir -p build

    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_PREFIX_PATH=/opt/rocm \
        -DBUILD_TESTS=OFF \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    cd projects/rocm-smi-lib
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    cd projects/rocm-smi-lib
    DESTDIR="$DESTDIR" cmake --install build
}
