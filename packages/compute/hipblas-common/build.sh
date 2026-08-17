#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# hipblas-common 7.2.4 — shared hipBLAS API headers
# Source: projects/hipblas-common inside rocm-libraries
#
# Header/cmake-config-only project (upstream versions it 1.x internally;
# the package carries the monorepo release version like the rest of the
# set). hipblas hard-depends on its cmake package at configure time.

configure() {
    set -e
    cd projects/hipblas-common
    mkdir -p build

    cmake -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_PREFIX_PATH=/opt/rocm \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    cd projects/hipblas-common
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    cd projects/hipblas-common
    DESTDIR="$DESTDIR" cmake --install build
}
