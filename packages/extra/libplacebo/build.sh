#!/bin/bash
# libplacebo 7.360.0 — GPU-accelerated image and video processing library
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..              \
          --prefix=/usr         \
          --buildtype=release   \
          -D tests=true         \
          -D demos=false
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
