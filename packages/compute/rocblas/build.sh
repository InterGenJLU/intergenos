#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# rocblas 7.2.4 — BLAS on ROCm (Tensile kernel generation)
# Source: projects/rocblas + shared/tensile inside rocm-libraries
#
# THE target-sensitive package: Tensile generates + compiles GPU kernels
# per declared gfx target (IGOS_GPU_TARGETS from package.yml gpu_targets;
# fail-closed if the declaration is missing — a silent default target
# set is exactly the class the declare model exists to prevent).
#
# OFFLINE-CHROOT DISCIPLINE (validated against the pinned sources):
# - rocm-cmake package preempts cmake/get-rocm-cmake.cmake's network
#   FetchContent (ROCmCMakeBuildTools resolves from /opt/rocm).
# - Tensile installs into a build venv via pip (cmake/virtualenv.cmake):
#   PIP_NO_INDEX=1 forbids any network resolution (fail-loud, never
#   fetch); PIP_NO_BUILD_ISOLATION=1 uses the system setuptools/wheel;
#   the venv is created --system-site-packages, so Tensile's
#   requirements (pyyaml, msgpack, joblib, rich) resolve from the
#   installed core packages. This venv path REQUIRES BUILD_WITH_PIP=ON
#   (the upstream default, cmake/build-options.cmake:98): OFF skips the
#   whole Tensile_TEST_LOCAL_PATH/virtualenv block and expects a system
#   TensileConfig.cmake we deliberately do not ship — it aborted
#   find_package(Tensile) on firing 5. "PIP" here means the install
#   MECHANISM, not the network; PIP_NO_INDEX keeps it offline.
# - Tensile's host lib reads msgpack logic files via the system
#   msgpack-cxx headers (find_package REQUIRED — nothing vendored).
#
# Clients (bench/tests) are NOT built: no gtest/cblas/gfortran in the
# closure; the library + kernels are the product.
# BUILD_WITH_HIPBLASLT=OFF: hipblaslt does not support our full declared
# target set (upstream's own packaging disables it for the same reason).
# -fcf-protection=none: unsupported by the HIP device compiler (rocmcc
# documented limitation) — host-side hardening flags that clash with
# device compilation are cleared for device-code packages only.

configure() {
    set -e
    GPU_TARGETS="${IGOS_GPU_TARGETS:?FATAL: gpu_targets not declared in package.yml/plumbing}"

    export PIP_NO_INDEX=1
    export PIP_NO_BUILD_ISOLATION=1
    export HIPCC_COMPILE_FLAGS_APPEND="-parallel-jobs=${IGOS_JOBS}"
    export HIPCC_LINK_FLAGS_APPEND="-parallel-jobs=${IGOS_JOBS}"

    # ROCM_PATH: amdclang++ deduces the ROCm/HIP root from its own RESOLVED
    # location (/opt/rocm/lib/llvm/bin -> candidate /opt/rocm/lib — clang walks
    # one dir up past "llvm" only, and /proc/self/exe defeats the /opt/rocm/bin
    # symlink entry point), then accepts it because the HIP version file is
    # found via the PARENT-share fallback (/opt/rocm/share/hip/version). The
    # rocm_check_target_ids probe's --hip-link then emits -L<root>/lib =
    # /opt/rocm/lib/lib (nonexistent) -> -lamdhip64 unresolved -> every gfx
    # probe "Failed" and configure aborts "Unsupported target" even though the
    # archs are supported. ROCM_PATH pins detection to /opt/rocm (probe-proven:
    # all three declared targets compile+link with it, none without). An env
    # var, not a CXX flag, so Tensile's subprocess compiles inherit it too.
    export ROCM_PATH=/opt/rocm

    cd projects/rocblas

    # pip 25.x mishandles the PIP_NO_BUILD_ISOLATION env var (probe-proven
    # in-chroot: the env form fails "No matching distribution found for
    # setuptools>=40.8.0" under PIP_NO_INDEX because build isolation stays
    # on; the explicit-flag form installs Tensile 4.45.0 offline clean).
    # In-package patch (same class as rocprofiler-register's CPackComponent
    # include): make the vendored venv pip invocation EXPLICITLY offline +
    # non-isolated, so the no-network guarantee lives in the invocation
    # itself, not in ambient env. Fail-loud if upstream reshapes the line.
    sed -i 's|-m pip install ${ARGN}|-m pip install --no-index --no-build-isolation ${ARGN}|' cmake/virtualenv.cmake
    grep -q -- '-m pip install --no-index --no-build-isolation ${ARGN}' cmake/virtualenv.cmake \
        || { echo "FATAL: virtualenv.cmake pip-flags patch did not apply"; exit 1; }

    mkdir -p build

    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_C_COMPILER=/opt/rocm/lib/llvm/bin/amdclang \
        -DCMAKE_CXX_COMPILER=/opt/rocm/lib/llvm/bin/amdclang++ \
        -DCMAKE_TOOLCHAIN_FILE=toolchain-linux.cmake \
        -DCMAKE_CXX_FLAGS="-Wno-unused -fcf-protection=none" \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_PREFIX_PATH=/opt/rocm \
        -Damd_comgr_DIR=/opt/rocm/lib/cmake/amd_comgr \
        -DHIP_PLATFORM=amd \
        -DBUILD_WITH_TENSILE=ON \
        -DTensile_LIBRARY_FORMAT=msgpack \
        -DTensile_TEST_LOCAL_PATH="${PWD}/../../shared/tensile" \
        -DTensile_COMPILER=hipcc \
        -DBUILD_WITH_PIP=ON \
        -DBUILD_WITH_HIPBLASLT=OFF \
        -DBUILD_CLIENTS_TESTS=OFF \
        -DBUILD_CLIENTS_BENCHMARKS=OFF \
        -DBUILD_CLIENTS_SAMPLES=OFF \
        -DGPU_TARGETS="${GPU_TARGETS}" \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    export PIP_NO_INDEX=1
    export PIP_NO_BUILD_ISOLATION=1
    export HIPCC_COMPILE_FLAGS_APPEND="-parallel-jobs=${IGOS_JOBS}"
    export HIPCC_LINK_FLAGS_APPEND="-parallel-jobs=${IGOS_JOBS}"
    export ROCM_PATH=/opt/rocm   # see configure(): HIP-root detection pin
    cd projects/rocblas
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    cd projects/rocblas
    DESTDIR="$DESTDIR" cmake --install build
}
