#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# rocm-cmake 7.2.4 — ROCmCMakeBuildTools
# https://github.com/ROCm/rocm-cmake
#
# CMake-modules-only package (no compiled artifacts). Build-time
# prerequisite of the rocm-libraries packages: rocwmma hard-requires
# ROCmCMakeBuildTools (find_package ... REQUIRED), hipblas-common uses
# rocm_setup_version, and rocblas/hipblas otherwise FetchContent-download
# rocm-cmake at configure time (cmake/get-rocm-cmake.cmake) — a network
# fetch that fails loudly in the offline chroot. Installing the modules
# system-side removes the network path entirely: the consumers'
# find_package(ROCmCMakeBuildTools ... PATHS ${ROCM_PATH}) resolves here.

configure() {
    set -e
    mkdir -p build
    cd    build

    cmake .. \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    : # Modules-only — nothing to compile
}

do_install() {
    set -e
    cd build
    make DESTDIR="$DESTDIR" install
}
