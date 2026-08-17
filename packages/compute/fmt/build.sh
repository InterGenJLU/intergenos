#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# fmt 12.2.0 — {fmt} C++ formatting library
#
# Shared library + CMake config package so consumers resolve it via
# find_package(fmt). Tests stay ON: fmt vendors its GTest fork in-tree
# (test/gtest), so the suite runs offline in the chroot.

configure() {
    set -e
    mkdir -p build

    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/usr \
        -DBUILD_SHARED_LIBS=ON \
        -DFMT_TEST=ON \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    cmake --build build -j "${IGOS_JOBS}"
}

check() {
    set -e
    ctest --test-dir build --output-on-failure -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
