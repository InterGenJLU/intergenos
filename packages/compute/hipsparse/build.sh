#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# hipsparse 7.2.4 — cuSPARSE-compatible interface over rocSPARSE
# Source: projects/hipsparse inside rocm-libraries
#
# BUILD_CLIENTS_BENCHMARKS/SAMPLES default ON upstream — explicitly OFF
# here (see package.yml note); tests already default OFF.

configure() {
    set -e
    GPU_TARGETS="${IGOS_GPU_TARGETS:?FATAL: gpu_targets not declared in package.yml/plumbing}"

    # ROCM_PATH: HIP-root detection pin (same class as rocblas/rocwmma).
    export ROCM_PATH=/opt/rocm

    cd projects/hipsparse
    mkdir -p build

    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_COMPILER=/opt/rocm/lib/llvm/bin/amdclang++ \
        -DCMAKE_CXX_FLAGS="-fcf-protection=none" \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_PREFIX_PATH=/opt/rocm \
        -DHIP_PLATFORM=amd \
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
    cd projects/hipsparse
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    cd projects/hipsparse
    DESTDIR="$DESTDIR" cmake --install build
}
