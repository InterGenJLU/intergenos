#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# hipblaslt 7.2.4 — TensileLite-generated GEMM library (heavy build)
# Source: projects/hipblaslt inside rocm-libraries, + pre-staged
# nanobind/robin-map for the rocisa module (Rule-5 explicit extraction;
# see package.yml for the offline-redirect design).

configure() {
    set -e
    GPU_TARGETS="${IGOS_GPU_TARGETS:?FATAL: gpu_targets not declared in package.yml/plumbing}"

    # ROCM_PATH: HIP-root detection pin (same class as rocblas/rocwmma).
    export ROCM_PATH=/opt/rocm

    # Rule 5: stage nanobind (+ its robin_map submodule tree) where the
    # FETCHCONTENT_SOURCE_DIR_NANOBIND override will consume it.
    NANOBIND_STAGE="$PWD/nanobind-staged"
    rm -rf "$NANOBIND_STAGE"
    tar -xzf "${IGOS_SOURCES}/nanobind-2.6.1.tar.gz"
    mv nanobind-9b3afa9dbdc23641daf26fadef7743e7127ff92f "$NANOBIND_STAGE"
    tar -xzf "${IGOS_SOURCES}/robin-map-188c4556.tar.gz"
    rm -rf "$NANOBIND_STAGE/ext/robin_map"
    mv robin-map-188c45569cc2a5dd768077c193830b51d33a5020 "$NANOBIND_STAGE/ext/robin_map"
    [ -f "$NANOBIND_STAGE/ext/robin_map/include/tsl/robin_map.h" ] || {
        echo "FATAL: staged nanobind/ext/robin_map is incomplete" >&2
        exit 1
    }

    cd projects/hipblaslt
    mkdir -p build

    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_COMPILER=/opt/rocm/lib/llvm/bin/amdclang++ \
        -DCMAKE_CXX_FLAGS="-fcf-protection=none" \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_PREFIX_PATH=/opt/rocm \
        -DHIP_PLATFORM=amd \
        -DFETCHCONTENT_SOURCE_DIR_NANOBIND="$NANOBIND_STAGE" \
        -DHIPBLASLT_ENABLE_CLIENT=OFF \
        -DHIPBLASLT_ENABLE_ROCROLLER=OFF \
        -DHIPBLASLT_ENABLE_MARKER=ON \
        -DHIPBLASLT_ENABLE_MSGPACK=ON \
        -DHIPBLASLT_ENABLE_OPENMP=ON \
        -DHIPBLASLT_BUNDLE_PYTHON_DEPS=ON \
        -DROCM_SYMLINK_LIBS=OFF \
        -DGPU_TARGETS="${GPU_TARGETS}" \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    export ROCM_PATH=/opt/rocm   # see configure(): HIP-root detection pin
    cd projects/hipblaslt
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    cd projects/hipblaslt
    DESTDIR="$DESTDIR" cmake --install build
}
