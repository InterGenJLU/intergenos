#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# rocwmma 7.2.4 — WMMA header library (header-only install)
# Source: projects/rocwmma inside rocm-libraries
#
# BUILD-ONLY dependency of llama-cpp-hip's GGML_HIP_ROCWMMA_FATTN=ON
# flash-attention path (freshness finding 4: compile the capability in;
# the runtime A/B decides the default -fa). Header-only: tests/samples
# OFF means nothing compiles here — the install stage lays headers +
# cmake config. Hard-requires ROCmCMakeBuildTools (validated: REQUIRED
# find_package at CMakeLists ~line 83 — the rocm-cmake package).

configure() {
    set -e
    GPU_TARGETS="${IGOS_GPU_TARGETS:?FATAL: gpu_targets not declared in package.yml/plumbing}"

    # ROCM_PATH: same HIP-root detection pin as rocblas (see that recipe for
    # the full mechanism). amdclang++ invoked at its real /opt/rocm/lib/llvm/bin
    # path deduces the HIP root as /opt/rocm/lib, so any --hip-link probe or
    # link emits the nonexistent -L/opt/rocm/lib/lib and fails on -lamdhip64.
    # Same class, same probe-proven fix.
    export ROCM_PATH=/opt/rocm

    cd projects/rocwmma
    mkdir -p build

    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_COMPILER=/opt/rocm/lib/llvm/bin/amdclang++ \
        -DCMAKE_CXX_FLAGS="-fcf-protection=none" \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_PREFIX_PATH=/opt/rocm \
        -DHIP_PLATFORM=amd \
        -DROCWMMA_BUILD_TESTS=OFF \
        -DROCWMMA_BUILD_SAMPLES=OFF \
        -DROCM_SYMLINK_LIBS=OFF \
        -DGPU_TARGETS="${GPU_TARGETS}" \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    export ROCM_PATH=/opt/rocm   # see configure(): HIP-root detection pin
    cd projects/rocwmma
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    cd projects/rocwmma
    DESTDIR="$DESTDIR" cmake --install build
}
