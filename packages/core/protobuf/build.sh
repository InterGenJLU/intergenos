#!/bin/bash
# protobuf 33.5 — Protocol Buffers serialization library
# BLFS 13.0

configure() {
    set -e
    cmake -B build                    \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5  \
          -DCMAKE_SKIP_INSTALL_RPATH=ON \
          -Dprotobuf_BUILD_SHARED_LIBS=ON \
          -Dprotobuf_BUILD_TESTS=OFF
}

build() {
    set -e
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
