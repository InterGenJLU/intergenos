#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# rocprim 7.2.4 — ROCm parallel primitives (header-only install)
# Source: projects/rocprim inside rocm-libraries
#
# Header-only: BUILD_TEST/BUILD_BENCHMARK/BUILD_EXAMPLE default OFF in
# the pinned CMakeLists (lines 66-73) and stay OFF — nothing compiles
# here; the install stage lays headers + cmake config that the rest of
# the math-library set consumes via find_package.

configure() {
    set -e
    GPU_TARGETS="${IGOS_GPU_TARGETS:?FATAL: gpu_targets not declared in package.yml/plumbing}"

    # ROCM_PATH: HIP-root detection pin (same class as rocblas/rocwmma —
    # amdclang++ resolved at /opt/rocm/lib/llvm/bin deduces the HIP root
    # as /opt/rocm/lib and emits a nonexistent -L/opt/rocm/lib/lib).
    export ROCM_PATH=/opt/rocm

    cd projects/rocprim
    mkdir -p build

    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_COMPILER=/opt/rocm/lib/llvm/bin/amdclang++ \
        -DCMAKE_CXX_FLAGS="-fcf-protection=none" \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_PREFIX_PATH=/opt/rocm \
        -DHIP_PLATFORM=amd \
        -DBUILD_TEST=OFF \
        -DBUILD_BENCHMARK=OFF \
        -DBUILD_EXAMPLE=OFF \
        -DROCM_SYMLINK_LIBS=OFF \
        -DGPU_TARGETS="${GPU_TARGETS}" \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    export ROCM_PATH=/opt/rocm   # see configure(): HIP-root detection pin
    cd projects/rocprim
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    cd projects/rocprim
    DESTDIR="$DESTDIR" cmake --install build
}
