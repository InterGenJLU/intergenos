#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2026 InterGenJLU
#
# rocm-debug-agent 7.2.4 — GPU debug agent
# Source: standalone ROCm/rocr_debug_agent repo at the rocm-7.2.4 tag

configure() {
    set -e
    export ROCM_PATH=/opt/rocm

    mkdir -p build
    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_PREFIX_PATH=/opt/rocm \
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
