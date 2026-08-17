#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2026 InterGenJLU
#
# aqlprofile 7.2.4 — HSA AQL profiling packets library
# Source: projects/aqlprofile inside rocm-systems
#
# AQLPROFILE_BUILD_TESTS defaults OFF (pinned CMakeLists:61) and stays OFF.

configure() {
    set -e
    export ROCM_PATH=/opt/rocm

    cd projects/aqlprofile
    mkdir -p build

    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_PREFIX_PATH=/opt/rocm \
        -DAQLPROFILE_BUILD_TESTS=OFF \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    export ROCM_PATH=/opt/rocm
    cd projects/aqlprofile
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    cd projects/aqlprofile
    DESTDIR="$DESTDIR" cmake --install build
}
