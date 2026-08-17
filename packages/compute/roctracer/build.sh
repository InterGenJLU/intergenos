#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2026 InterGenJLU
#
# roctracer 7.2.4 — runtime tracing + ROCTX markers
# Source: projects/roctracer inside rocm-systems

configure() {
    set -e
    # ROCM_PATH: HIP-root detection pin (same class as rocblas/rocwmma).
    export ROCM_PATH=/opt/rocm

    cd projects/roctracer
    mkdir -p build

    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_PREFIX_PATH=/opt/rocm \
        -DHIP_PLATFORM=amd \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    export ROCM_PATH=/opt/rocm
    cd projects/roctracer
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    cd projects/roctracer
    DESTDIR="$DESTDIR" cmake --install build
}
