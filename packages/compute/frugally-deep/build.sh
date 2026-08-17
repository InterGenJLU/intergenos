#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# frugally-deep @38c52448 — header-only Keras inference (MIOpen pin)
#
# Resolves FunctionalPlus/Eigen3/nlohmann_json from the installed
# packages (all three are declared build deps and land before this in
# the wave order).

configure() {
    set -e
    mkdir -p build
    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/usr \
        -DFDEEP_BUILD_UNITTEST=OFF \
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
