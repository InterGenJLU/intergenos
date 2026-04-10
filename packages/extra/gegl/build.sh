#!/bin/bash
# gegl 0.4.66 — GEneric Graphics Library (image processing framework)
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..              \
          --prefix=/usr         \
          --libdir=/usr/lib     \
          --buildtype=release
}

build() {
    cd build
    ninja
}

check() {
    cd build
    ninja test || true
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install

}
