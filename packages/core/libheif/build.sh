#!/bin/bash
# libheif 1.21.2 — HEIF and AVIF file format decoder and encoder
# BLFS 13.0

configure() {
    set -e
    mkdir -p build
    cd    build

    cmake -D CMAKE_INSTALL_PREFIX=/usr \
          -D CMAKE_BUILD_TYPE=Release  \
          -D CMAKE_POLICY_VERSION_MINIMUM=3.5 \
          -D WITH_GDK_PIXBUF=OFF       \
          -D WITH_OpenH264_DECODER=OFF \
          -G Ninja ..
}

build() {
    set -e
    cd build
    ninja
}

check() {
    set -e
    cd build
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        ninja test
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
}
