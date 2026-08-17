#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# aotriton-triton-llvm 21.0.0 — pristine LLVM/MLIR/LLD at the commit AOTriton
# 0.11.1b's bundled triton fork (aotriton-hyperjump @ f75e44a) pins
# (cmake/llvm-hash.txt = 570885128), installed to the private prefix
# /opt/aotriton-llvm. Consumed ONLY by the aotriton recipe
# (LLVM_SYSPATH=/opt/aotriton-llvm) for building that bundled triton.
#
# Identical build shape to compute/triton-llvm (only the pinned commit and the
# install prefix differ) — the fork's CMake requirement set was re-verified
# against ITS own CMakeLists and matches upstream triton's: find_package(MLIR
# CONFIG) + the AMD backend's find_package(LLD CONFIG) linking lldCommon/lldELF,
# include(TableGen), and TRITON_LIBRARIES linking both LLVMAMDGPUCodeGen and
# LLVMNVPTXCodeGen unconditionally (so both GPU targets are required). No clang.

configure() {
    set -e
    mkdir -p build
    cmake -G Ninja -S llvm -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/aotriton-llvm \
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
