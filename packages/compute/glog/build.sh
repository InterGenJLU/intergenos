#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# glog 0.7.1 — google logging library
#
# Feature scope deliberately mirrors the configuration its sole consumer
# (rocprofiler-register) builds and ships upstream in its bundled path:
# WITH_GFLAGS=OFF, WITH_GTEST=OFF, WITH_UNWIND=none (values verified in
# the pinned sources: CMakeLists.txt options block, and the consumer's
# external/CMakeLists.txt). gflags and libunwind both exist in-tree, so
# this is a documented scope decision, not a missing-dep bypass — widen
# it when a consumer needs those integrations. Tests are off in the
# recipe (see package.yml): WITH_GTEST pulls an external GTest, which is
# offline-fatal in the chroot.

configure() {
    set -e
    mkdir -p build

    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/usr \
        -DBUILD_SHARED_LIBS=ON \
        -DWITH_GFLAGS=OFF \
        -DWITH_GTEST=OFF \
        -DWITH_UNWIND=none \
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
