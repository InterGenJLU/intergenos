#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# hipsolver 7.2.4 — cuSOLVER-compatible interface over rocSOLVER
# Source: projects/hipsolver inside rocm-libraries
#
# BUILD_WITH_SPARSE stays at the upstream default OFF: hipsolver's flag
# has DIFFERENT semantics than rocsolver's — ON additionally demands
# SuiteSparse (find_package(CHOLMOD REQUIRED), library/src/CMakeLists
# .txt:163-167), which this tree deliberately does not ship (decision
# record R4: the hipsolverSp cholesky/QR routes dlopen libcholmod at
# runtime and fail loudly until a SuiteSparse chain lands). OFF still
# links rocblas + rocsolver as REQUIRED (CMakeLists:116-139) — the
# dense + Rf surfaces are fully link-checked; only the Sp routes use
# runtime dlopen, per the recorded design. (First proof firing had ON
# by conflation with rocsolver's flag; halted at the CHOLMOD wall.)
# Fortran bindings keep their upstream UNIX default ON (gfortran via
# gcc-core r4). Clients stay OFF.

configure() {
    set -e
    GPU_TARGETS="${IGOS_GPU_TARGETS:?FATAL: gpu_targets not declared in package.yml/plumbing}"

    # ROCM_PATH: HIP-root detection pin (same class as rocblas/rocwmma).
    export ROCM_PATH=/opt/rocm

    cd projects/hipsolver
    mkdir -p build

    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_COMPILER=/opt/rocm/lib/llvm/bin/amdclang++ \
        -DCMAKE_CXX_FLAGS="-fcf-protection=none" \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_PREFIX_PATH=/opt/rocm \
        -DHIP_PLATFORM=amd \
        -DBUILD_WITH_SPARSE=OFF \
        -DBUILD_FORTRAN_BINDINGS=ON \
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
    cd projects/hipsolver
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    cd projects/hipsolver
    DESTDIR="$DESTDIR" cmake --install build
}
