#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# thrift 0.24.0 — C++ library + compiler only (the arrow-cpp/parquet
# consumer needs libthrift; the compiler ships as the package's natural
# primary binary). All other language bindings OFF — that is scope
# selection for a single-consumer C++ dep, not a feature bypass: the
# bindings serve ecosystems (Java/Python/JS) this package does not target.

configure() {
    set -e
    cmake -B build -G Ninja -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_BUILD_TYPE=Release \
          -DBUILD_COMPILER=ON \
          -DBUILD_CPP=ON \
          -DBUILD_SHARED_LIBS=ON \
          -DBUILD_JAVA=OFF -DBUILD_JAVASCRIPT=OFF -DBUILD_NODEJS=OFF \
          -DBUILD_PYTHON=OFF -DBUILD_C_GLIB=OFF \
          -DBUILD_TESTING=OFF -DBUILD_TUTORIALS=OFF
}

build() {
    set -e
    cmake --build build
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
