#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# zig 0.16.0 — The Zig programming language compiler and toolchain
# Not in BLFS — InterGenOS core-tier language toolchain (RC001 unlock lane).
#
# zig is self-hosted: the source ships a C bootstrap (bootstrap.c) that builds a
# minimal zig, which then compiles the real compiler. The standard path is the
# CMake build, which drives that sequence to produce stage3/bin/zig — the Zig
# compiler built by itself (README "Building from Source"). It links the
# LLVM/Clang/LLD 21.x development libraries; the in-tree llvm (21.1.8) provides
# them, so CMAKE_PREFIX_PATH=/usr lets CMake find llvm-config. Our llvm is a
# shared build, so ZIG_SHARED_LLVM=ON (the default prefers static libLLVM, which
# the in-tree llvm does not ship).

configure() {
    set -e
    mkdir -pv build
    cd build
    cmake .. \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/usr \
        -DCMAKE_PREFIX_PATH=/usr \
        -DZIG_SHARED_LLVM=ON \
        -DZIG_STATIC_LLVM=OFF
}

build() {
    set -e
    # Each phase is a fresh `source build.sh`; the build/ dir persists on disk.
    cd build
    # env -u DESTDIR: the builder exports do_install's staging DESTDIR for the
    # whole package run, and zig's own build runner honors it — the stage3
    # bootstrap install (`zig2 build --prefix .../build/stage3`, a BUILD-phase
    # step) silently redirects under the staging root (exit 0, binary at
    # <staging>/<abs-prefix>, check() 127). Same class as the pip/cmake
    # DESTDIR-redirect guards; do_install's explicit DESTDIR= install below is
    # the one place it belongs.
    env -u DESTDIR make -j"$(nproc)"
}

check() {
    set -e
    cd build
    # stage3 zig built itself — a version print proves the self-hosted compiler runs.
    ./stage3/bin/zig version
}

do_install() {
    set -e
    cd build
    make DESTDIR="${DESTDIR}" install
}
