#!/bin/bash
# gegl 0.4.66 — GEneric Graphics Library (image processing framework)
# BLFS 13.0

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..              \
          --prefix=/usr         \
          --libdir=/usr/lib     \
          --buildtype=release
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
