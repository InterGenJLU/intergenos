#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# llvm 21.1.8 — LLVM compiler infrastructure
# BLFS 13.0
# Note: requires clang, cmake-modules, and third-party tarballs in sources dir

pre_configure() {
    # pipefail: grep | xargs pipe on line 29
    set -e -o pipefail
    # Extract additional required tarballs
    tar -xf "${IGOS_SOURCES}/llvm-cmake-${version}.src.tar.xz"
    tar -xf "${IGOS_SOURCES}/llvm-third-party-${version}.src.tar.xz"

    # Fix paths to extracted cmake and third-party directories
    sed "/LLVM_COMMON_CMAKE_UTILS/s@../cmake@cmake-${version}.src@" \
        -i CMakeLists.txt
    sed "/LLVM_THIRD_PARTY_DIR/s@../third-party@third-party-${version}.src@" \
        -i cmake/modules/HandleLLVMOptions.cmake

    # Extract clang into the source tree
    tar -xf "${IGOS_SOURCES}/clang-${version}.src.tar.xz" -C tools
    mv tools/clang-${version}.src tools/clang

    # Extract lld into the source tree (release 4) — built via the same
    # in-tree tools/ mechanism as clang; installs ld.lld + /usr/include/lld
    # + liblld* (zig hard-requires all three).
    tar -xf "${IGOS_SOURCES}/lld-${version}.src.tar.xz" -C tools
    mv tools/lld-${version}.src tools/lld

    # lld's MachO port includes headers from a SIBLING libunwind source
    # dir (MachO/CMakeLists.txt: ${LLVM_MAIN_SRC_DIR}/../libunwind/include).
    # Header extraction only — libunwind is never built or installed.
    rm -rf ../libunwind
    tar -xf "${IGOS_SOURCES}/libunwind-${version}.src.tar.xz" -C ..
    mv ../libunwind-${version}.src ../libunwind

    # Extract compiler-rt if available
    if [ -f "${IGOS_SOURCES}/compiler-rt-${version}.src.tar.xz" ]; then
        tar -xf "${IGOS_SOURCES}/compiler-rt-${version}.src.tar.xz" -C projects
        mv projects/compiler-rt-${version}.src projects/compiler-rt
    fi

    # Fix Python scripts to use python3
    grep -rl '#!.*python' | xargs sed -i '1s/python$/python3/'

    # Ensure FileCheck is installed (needed by rust test suite and others)
    sed 's/utility/tool/' -i utils/FileCheck/CMakeLists.txt
}

configure() {
    set -e
    pre_configure

    mkdir -v build
    cd       build

    CC=gcc CXX=g++                                   \
    cmake -D CMAKE_INSTALL_PREFIX=/usr               \
          -D CMAKE_SKIP_INSTALL_RPATH=ON             \
          -D LLVM_ENABLE_FFI=ON                      \
          -D CMAKE_BUILD_TYPE=Release                \
          -D LLVM_BUILD_LLVM_DYLIB=ON                \
          -D LLVM_LINK_LLVM_DYLIB=ON                 \
          -D LLVM_ENABLE_RTTI=ON                     \
          -D LLVM_TARGETS_TO_BUILD=all               \
          -D LLVM_BINUTILS_INCDIR=/usr/include       \
          -D LLVM_INCLUDE_BENCHMARKS=OFF             \
          -D CLANG_DEFAULT_PIE_ON_LINUX=ON           \
          -D CLANG_CONFIG_FILE_SYSTEM_DIR=/etc/clang \
          -D CMAKE_POLICY_VERSION_MINIMUM=3.5 \
          -W no-dev -G Ninja ..
}

build() {
    set -e
    cd build
    ninja
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install

    # Clang configuration ships as owned payload (hook-contract wave):
    #   --gcc-triple             — custom triple x86_64-igos-linux-gnu (clang's
    #                              GCCInstallationDetector doesn't know it)
    #   -fstack-protector-strong — BLFS SSP hardening
    # Byte-identical to the files the retired post_install wrote (644).
    install -dm755 "${DESTDIR}/etc/clang"
    for i in clang clang++; do
        cat > "${DESTDIR}/etc/clang/$i.cfg" <<'CFGEOF'
--gcc-triple=x86_64-igos-linux-gnu
-fstack-protector-strong
CFGEOF
        chmod 644 "${DESTDIR}/etc/clang/$i.cfg"
    done
}

