#!/bin/bash
# libpsl 0.21.5 — Public Suffix List library
# BLFS 13.0

configure() {
    set -e
    mkdir build
    cd    build

    meson setup --prefix=/usr     \
        --libdir=/usr/lib         \
        --buildtype=release ..
}

build() {
    set -e
    cd build
    ninja
}

check() {
    set -e
    cd build
    ninja test || true
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
}
