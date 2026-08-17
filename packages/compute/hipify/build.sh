#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2026 InterGenJLU
#
# hipify 7.2.4 — hipify-clang + hipify-perl
# Source: standalone ROCm/HIPIFY repo at the rocm-7.2.4 tag
#
# Links the shipped rocm-llvm's clang libraries; tests OFF (default —
# they want lit + a CUDA SDK, neither shipped by design).

configure() {
    set -e
    export ROCM_PATH=/opt/rocm

    mkdir -p build
    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_PREFIX_PATH="/opt/rocm/lib/llvm;/opt/rocm" \
        -DHIPIFY_CLANG_TESTS=OFF \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    export ROCM_PATH=/opt/rocm
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
