#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# rocsolver 7.2.4 — ROCm dense LAPACK-class solvers (long Tensile-adjacent build)
# Source: projects/rocsolver inside rocm-libraries
#
# Requires gfortran in the chroot (pinned CMakeLists:37-38 — the wave's
# lifted FULL HALT; gcc-core r4). BUILD_TESTING/clients default OFF and
# stay OFF. BUILD_WITH_SPARSE=ON links the installed rocsparse instead
# of the upstream shared-build default (runtime dlopen) — see package.yml.

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    GPU_TARGETS="${IGOS_GPU_TARGETS:?FATAL: gpu_targets not declared in package.yml/plumbing}"

    # Upstream guard bug under BUILD_WITH_SPARSE=ON: rfinfo's dlopen-fallback
    # loader (load_function/load_rocsparse/try_load_rocsparse) is guarded only
    # by ROCSOLVER_STATIC_LIB, while its dlfcn.h include and the g_sparse_*
    # declarations (library/src/include/rocsparse.hpp:172-790) sit under
    # !HAVE_ROCSPARSE — so the HAVE_ROCSPARSE build compiles the fallback
    # bodies with no declarations and fails. The call site
    # (rocsolver_create_rfinfo) is already correctly !HAVE_ROCSPARSE-guarded;
    # the patch adds the same guard around the definitions.
    patch -p1 < "${IGOS_PACKAGE_DIR:-$BUILD_DIR}/patches/0001-rfinfo-guard-dlopen-fallback-under-HAVE_ROCSPARSE.patch"

    # ROCM_PATH: HIP-root detection pin (same class as rocblas/rocwmma).
    export ROCM_PATH=/opt/rocm

    cd projects/rocsolver
    mkdir -p build

    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_COMPILER=/opt/rocm/lib/llvm/bin/amdclang++ \
        -DCMAKE_CXX_FLAGS="-fcf-protection=none" \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_PREFIX_PATH=/opt/rocm \
        -DHIP_PLATFORM=amd \
        -DBUILD_WITH_SPARSE=ON \
        -DBUILD_TESTING=OFF \
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
    cd projects/rocsolver
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    cd projects/rocsolver
    DESTDIR="$DESTDIR" cmake --install build
}
