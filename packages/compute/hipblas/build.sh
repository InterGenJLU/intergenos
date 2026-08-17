#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# hipblas 7.2.4 — BLAS marshalling API over rocBLAS
# Source: projects/hipblas inside rocm-libraries
#
# BUILD_WITH_SOLVER=ON (Decided 2026-07-22): upstream's supported toggle
# (CMakeLists ~line 67) for the hipblas<->rocSOLVER LAPACK marshalling
# functions. Originally OFF when llama.cpp's HIP backend (BLAS/GEMM only)
# was the sole consumer and rocsolver was not in the compute set — the
# recipe reserved the flip as a reviewed change for the first LAPACK
# consumer. That consumer is pytorch 2.10.0: libtorch_hip.so references
# the 16 solver-marshalled routines (gels/geqrf/getrf/getrs x S/D/C/Z
# Batched), and import fails with undefined hipblas*gelsBatched against
# a solver-less libhipblas. rocsolver is in the compute set now
# (rocsolver, hipsolver); declared as build+runtime dep with this flip.
#
# Compiled with hipcc (the upstream-documented compiler for this
# package); -fcf-protection=none per the documented HIP limitation.
# Clients/tests not built.

configure() {
    set -e

    # ROCM_PATH: same HIP-root detection pin as rocblas/rocwmma (see the
    # rocblas recipe for the full mechanism). hipcc is NOT exempt — it
    # self-locates its own variables but delegates the final link to
    # clang++ --hip-link, whose detector mis-lands on /opt/rocm/lib and
    # emits the nonexistent -L/opt/rocm/lib/lib -> -lamdhip64 unresolved
    # (live-proven: the CMake compiler probe failed exactly there).
    export ROCM_PATH=/opt/rocm

    cd projects/hipblas
    mkdir -p build

    cmake -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_COMPILER=/opt/rocm/bin/hipcc \
        -DCMAKE_CXX_FLAGS="-fcf-protection=none" \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_PREFIX_PATH=/opt/rocm \
        -DHIP_PLATFORM=amd \
        -DBUILD_WITH_SOLVER=ON \
        -DBUILD_CLIENTS_TESTS=OFF \
        -DBUILD_CLIENTS_BENCHMARKS=OFF \
        -DBUILD_CLIENTS_SAMPLES=OFF \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    export ROCM_PATH=/opt/rocm   # see configure(): HIP-root detection pin
    cd projects/hipblas
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    cd projects/hipblas
    DESTDIR="$DESTDIR" cmake --install build
}
