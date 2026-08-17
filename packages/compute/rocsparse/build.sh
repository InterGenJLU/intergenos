#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# rocsparse 7.2.4 — ROCm sparse linear algebra library
# Source: projects/rocsparse inside rocm-libraries
#
# Clients/tests default OFF (pinned CMakeLists:79-87) and stay OFF.
# BUILD_WITH_ROCBLAS keeps its upstream default ON against the installed
# rocblas package.

configure() {
    set -e
    GPU_TARGETS="${IGOS_GPU_TARGETS:?FATAL: gpu_targets not declared in package.yml/plumbing}"

    # ROCM_PATH: HIP-root detection pin (same class as rocblas/rocwmma).
    export ROCM_PATH=/opt/rocm

    cd projects/rocsparse
    mkdir -p build

    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_COMPILER=/opt/rocm/lib/llvm/bin/amdclang++ \
        -DCMAKE_CXX_FLAGS="-fcf-protection=none" \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_PREFIX_PATH=/opt/rocm \
        -DHIP_PLATFORM=amd \
        -DBUILD_WITH_ROCBLAS=ON \
        -DBUILD_CLIENTS_TESTS=OFF \
        -DBUILD_CLIENTS_BENCHMARKS=OFF \
        -DBUILD_CLIENTS_SAMPLES=OFF \
        -DROCM_SYMLINK_LIBS=OFF \
        -DGPU_TARGETS="${GPU_TARGETS}" \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    export ROCM_PATH=/opt/rocm   # see configure(): HIP-root detection pin
    cd projects/rocsparse
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    cd projects/rocsparse
    DESTDIR="$DESTDIR" cmake --install build
}
