#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2026 InterGenJLU
#
# migraphx 7.2.4 — graph inference engine (ONNX/TF import, GPU JIT)
# Source: standalone ROCm/AMDMIGraphX repo at the rocm-7.2.4 tag
#
# MLIR + composable-kernel backends OFF per package.yml (CDNA-scoped;
# MLIR = the rocMLIR decision item, CK jit_library = gfx9-only in the
# pinned CK). Python bindings ON against the installed pybind11.

# Patch source dir: resolve this script's own location (works under both the
# driver's IGOS_PACKAGE_DIR and a bare source of build.sh).
BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    GPU_TARGETS="${IGOS_GPU_TARGETS:?FATAL: gpu_targets not declared in package.yml/plumbing}"

    # Upstream guard slip (7.2.4, rocsolver-rfinfo class): mlir.cpp compiles
    # unconditionally as the stub path when MLIR is OFF, but its
    # <mlir-c/Dialect/RockEnums.h> include sits ABOVE the #ifdef MIGRAPHX_MLIR
    # block -> fatal include error with no rocMLIR present. Every RockEnums
    # consumer in the file is inside the guard; the patch moves the include in
    # beside its sibling <mlir-c/Dialect/Rock.h>.
    patch -p1 < "${IGOS_PACKAGE_DIR:-$BUILD_DIR}/patches/0001-mlir-guard-rockenums-include-under-MIGRAPHX_MLIR.patch"

    # ROCM_PATH: HIP-root detection pin (same class as rocblas/rocwmma).
    export ROCM_PATH=/opt/rocm

    mkdir -p build
    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_COMPILER=/opt/rocm/lib/llvm/bin/amdclang++ \
        -DCMAKE_CXX_FLAGS="-fcf-protection=none" \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_PREFIX_PATH="/opt/rocm;/usr" \
        -DHIP_PLATFORM=amd \
        -DMIGRAPHX_ENABLE_PYTHON=ON \
        -DMIGRAPHX_USE_MIOPEN=ON \
        -DMIGRAPHX_USE_ROCBLAS=ON \
        -DMIGRAPHX_USE_HIPBLASLT=ON \
        -DMIGRAPHX_ENABLE_MLIR=OFF \
        -DMIGRAPHX_USE_COMPOSABLEKERNEL=OFF \
        -DBUILD_TESTING=OFF \
        -DGPU_TARGETS="${GPU_TARGETS}" \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    export ROCM_PATH=/opt/rocm   # see configure(): HIP-root detection pin
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
