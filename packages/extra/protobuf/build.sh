#!/bin/bash
# protobuf 29.6 — Protocol Buffers compiler and runtime library
# Upstream: https://github.com/protocolbuffers/protobuf

configure() {
    set -e
    mkdir -p build
    cd    build

    cmake \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/usr \
        -DCMAKE_INSTALL_LIBDIR=lib \
        -Dprotobuf_BUILD_SHARED_LIBS=ON \
        -Dprotobuf_BUILD_TESTS=OFF \
        -Dprotobuf_BUILD_CONFORMANCE=OFF \
        -Dprotobuf_BUILD_EXAMPLES=OFF \
        -Dprotobuf_INSTALL=ON \
        -Dprotobuf_ABSL_PROVIDER=package \
        -Dprotobuf_USE_EXTERNAL_GTEST=OFF \
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
    cd build
    # Tests disabled via -Dprotobuf_BUILD_TESTS=OFF
    true
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
}
