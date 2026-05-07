#!/bin/bash
# Kmod 34.2
# LFS 13.0 Section 8.60
#
# Uses meson. DESTDIR supported natively.

configure() {
    set -e
    mkdir -p build
    cd       build

    meson setup --prefix=/usr ..    \
        --libdir=/usr/lib           \
        --buildtype=release         \
        -D manpages=false
}

build() {
    set -e
    cd build
    ninja -j${IGOS_JOBS}
}

check() {
    set -e
    : # Tests require raw kernel headers, beyond LFS scope
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
}
