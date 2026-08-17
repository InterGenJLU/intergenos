#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# rocfft 7.2.4 — ROCm FFT library (heavy device-kernel build)
# Source: projects/rocfft inside rocm-libraries
#
# Clients/tests default OFF and stay OFF. SQLITE_USE_SYSTEM_PACKAGE=ON
# (see package.yml — system sqlite for the runtime kernel cache instead
# of the default FetchContent download). USE_HIPRAND keeps its upstream
# default ON (device-side input generation surface; hiprand is installed
# before rocfft in the wave order). ROCFFT_MPI_ENABLE stays OFF.

configure() {
    set -e
    GPU_TARGETS="${IGOS_GPU_TARGETS:?FATAL: gpu_targets not declared in package.yml/plumbing}"

    # ROCM_PATH: HIP-root detection pin (same class as rocblas/rocwmma).
    export ROCM_PATH=/opt/rocm

    cd projects/rocfft
    mkdir -p build

    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_COMPILER=/opt/rocm/lib/llvm/bin/amdclang++ \
        -DCMAKE_CXX_FLAGS="-fcf-protection=none" \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_PREFIX_PATH=/opt/rocm \
        -DHIP_PLATFORM=amd \
        -DSQLITE_USE_SYSTEM_PACKAGE=ON \
        -DROCFFT_MPI_ENABLE=OFF \
        -DBUILD_CLIENTS_TESTS=OFF \
        -DROCM_SYMLINK_LIBS=OFF \
        -DGPU_TARGETS="${GPU_TARGETS}" \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    export ROCM_PATH=/opt/rocm   # see configure(): HIP-root detection pin
    cd projects/rocfft
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    cd projects/rocfft
    DESTDIR="$DESTDIR" cmake --install build
}
