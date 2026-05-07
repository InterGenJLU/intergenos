#!/bin/bash
# libnfs 6.0.2 — NFS client library
# Not in BLFS — standard cmake

configure() {
    set -e
    cmake -B build                            \
          -DCMAKE_INSTALL_PREFIX=/usr         \
          -DCMAKE_BUILD_TYPE=Release
}

build() {
    set -e
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
