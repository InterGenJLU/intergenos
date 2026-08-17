#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# eigen 3.4.0 — header-only linear algebra (install-only cmake run)
#
# BUILD_TESTING=OFF: eigen's test suite is enormous and its configure
# probes fortran/CUDA toolchains it doesn't need for the header install.

configure() {
    set -e
    mkdir -p build
    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/usr \
        -DBUILD_TESTING=OFF \
        -DEIGEN_BUILD_DOC=OFF \
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
