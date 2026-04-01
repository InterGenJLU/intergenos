#!/bin/bash
# Kmod 34.2
# LFS 13.0 Section 8.60
#
# Uses meson. DESTDIR supported natively.

configure() {
    mkdir -p build
    cd       build

    meson setup --prefix=/usr ..    \
        --buildtype=release         \
        -D manpages=false
}

build() {
    cd build
    ninja -j${IGOS_JOBS}
}

check() {
    : # Tests require raw kernel headers, beyond LFS scope
}

install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
