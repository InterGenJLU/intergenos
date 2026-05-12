#!/bin/bash
# snappy 1.2.2 — Google compression library
# Upstream: https://github.com/google/snappy
# License: BSD-3-Clause
# Downstream: LevelDB, RocksDB, MongoDB

configure() {
    set -e
    mkdir -p build
    cd    build

    cmake \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/usr \
        -DCMAKE_INSTALL_LIBDIR=lib \
        -DBUILD_SHARED_LIBS=ON \
        -DSNAPPY_BUILD_TESTS=OFF \
        -DSNAPPY_BUILD_BENCHMARKS=OFF \
        -G Ninja \
        ..
}

build() {
    set -e
    cd build
    ninja
}

check() {
    set -e
    # Tests disabled via -DSNAPPY_BUILD_TESTS=OFF
    true
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
}
