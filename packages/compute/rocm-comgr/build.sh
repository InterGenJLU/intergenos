#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# rocm-comgr 7.2.4 — Code Object Manager
# Source: amd/comgr inside the ROCm llvm-project tarball
#
# comgr's CMake hard-requires (find_package REQUIRED CONFIG) the
# AMDDeviceLibs, Clang, and LLD cmake packages — CMAKE_PREFIX_PATH names
# both the rocm-llvm install (Clang/LLD configs) and /opt/rocm
# (AMDDeviceLibs config from rocm-device-libs). Links the LLVM/Clang
# static archives into libamd_comgr.so (which is why rocm-llvm keeps its
# .a set installed). BUILD_TESTING=OFF: the test programs compile+run
# device code — meaningless in the GPU-less chroot.

configure() {
    set -e
    cd amd/comgr
    mkdir -p build

    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_PREFIX_PATH="/opt/rocm/lib/llvm;/opt/rocm" \
        -DBUILD_TESTING=OFF \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    cd amd/comgr
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    cd amd/comgr
    DESTDIR="$DESTDIR" cmake --install build
}
