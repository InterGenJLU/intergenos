#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# msgpack-cxx 8.0.0 — MessagePack for C++ (header-only)
# https://github.com/msgpack/msgpack-c (cpp_master line)
#
# Header-only C++ serialization library. Build-time dependency of the
# compute tier's rocblas: Tensile's host library reads msgpack-format
# kernel logic files and its CMake does find_package(msgpack-cxx CONFIG)
# with a REQUIRED fallback — nothing is vendored in the rocm-libraries
# monorepo, so the headers must be system-installed. Header-only usage:
# nothing links against a msgpack runtime library (none is built).
#
# MSGPACK_USE_BOOST=OFF: upstream's boost adaptors are optional and
# boost is not in the tree; OFF is the documented no-boost build and
# rocblas/Tensile does not use the boost adaptors.

configure() {
    set -e
    mkdir -p build
    cd    build

    cmake .. \
        -DCMAKE_INSTALL_PREFIX=/usr \
        -DCMAKE_BUILD_TYPE=Release \
        -DMSGPACK_USE_BOOST=OFF \
        -DMSGPACK_BUILD_DOCS=OFF \
        -DMSGPACK_BUILD_EXAMPLES=OFF \
        -DMSGPACK_BUILD_TESTS=OFF \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    : # Header-only — nothing to compile; install stage copies headers + cmake config
}

do_install() {
    set -e
    cd build
    make DESTDIR="$DESTDIR" install
}
