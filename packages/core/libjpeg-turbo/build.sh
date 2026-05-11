#!/bin/bash
# libjpeg-turbo 3.1.3 — High-speed JPEG compression/decompression library
# BLFS 13.0

configure() {
    set -e
    cmake -B build                    \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5  \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_INSTALL_DEFAULT_LIBDIR=lib
}

build() {
    set -e
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
