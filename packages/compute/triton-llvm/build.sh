#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# triton-llvm 22.0.0 — pristine LLVM/MLIR/LLD at triton 3.6.0's pinned commit
# (f6ded0be), installed to the private prefix /opt/triton-llvm. Consumed only
# by the triton recipe (LLVM_SYSPATH=/opt/triton-llvm).
#
# Requirement set derived from triton's CMake (declared against reality):
#   - projects mlir + lld: triton's root CMakeLists does
#     find_package(MLIR REQUIRED CONFIG) and the AMD backend does
#     find_package(LLD REQUIRED CONFIG) and links lldCommon/lldELF.
#   - targets AMDGPU + NVPTX + Native: TRITON_LIBRARIES is NOT backend-gated
#     — it links both LLVMAMDGPUCodeGen and LLVMNVPTXCodeGen (and the MLIR
#     ROCDL + NVVM dialects) unconditionally, so both GPU targets must exist
#     even for an AMD-only triton; Native provides the host/JIT target.
#   - static libs (no dylib): triton links the individual component archives.
#   - LLVM_INSTALL_UTILS=ON: triton references ${LLVM_SYSPATH}/bin/FileCheck.
# No clang project is built (triton does not use it), so the amdclang
# GCC-triple detection patch that rocm-llvm carries is not needed here.

configure() {
    set -e
    mkdir -p build
    cmake -G Ninja -S llvm -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/triton-llvm \
        -DLLVM_HOST_TRIPLE=x86_64-pc-linux-gnu \
        -DLLVM_ENABLE_PROJECTS="mlir;lld" \
        -DLLVM_TARGETS_TO_BUILD="AMDGPU;NVPTX;Native" \
        -DLLVM_INSTALL_UTILS=ON \
        -DLLVM_BUILD_LLVM_DYLIB=OFF \
        -DLLVM_LINK_LLVM_DYLIB=OFF \
        -DLLVM_ENABLE_BINDINGS=OFF \
        -DMLIR_ENABLE_BINDINGS_PYTHON=OFF \
        -DLLVM_ENABLE_OCAMLDOC=OFF \
        -DLLVM_INCLUDE_BENCHMARKS=OFF \
        -DLLVM_INCLUDE_EXAMPLES=OFF \
        -DLLVM_BUILD_TESTS=OFF \
        -DLLVM_INCLUDE_TESTS=OFF \
        -DLLVM_ENABLE_ZLIB=ON \
        -DLLVM_ENABLE_ZSTD=ON \
        -DLLVM_ENABLE_LIBXML2=ON \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
