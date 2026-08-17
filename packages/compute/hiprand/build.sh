#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# hiprand 7.2.4 — cuRAND-compatible interface over rocRAND
# Source: projects/hiprand inside rocm-libraries
#
# BUILD_TEST/BUILD_BENCHMARK/BUILD_FORTRAN_WRAPPER default OFF in the
# pinned CMakeLists (lines 47-56) and stay OFF; resolves the installed
# rocrand package (DOWNLOAD_ROCRAND=OFF default).

configure() {
    set -e
    GPU_TARGETS="${IGOS_GPU_TARGETS:?FATAL: gpu_targets not declared in package.yml/plumbing}"

    # ROCM_PATH: HIP-root detection pin (same class as rocblas/rocwmma).
    export ROCM_PATH=/opt/rocm

    cd projects/hiprand
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
        -DBUILD_FORTRAN_WRAPPER=OFF \
        -DROCM_SYMLINK_LIBS=OFF \
        -DGPU_TARGETS="${GPU_TARGETS}" \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    export ROCM_PATH=/opt/rocm   # see configure(): HIP-root detection pin
    cd projects/hiprand
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    cd projects/hiprand
    DESTDIR="$DESTDIR" cmake --install build
}
