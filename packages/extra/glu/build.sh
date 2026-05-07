#!/bin/bash
# glu 9.0.3 — Mesa OpenGL Utility library
# BLFS 13.0

configure() {
    set -e
    mkdir build
    cd    build

    meson setup ..              \
          --prefix=/usr         \
          --libdir=/usr/lib     \
          --buildtype=release   \
          -D gl_provider=gl     \
          -D default_library=shared
}

build() {
    set -e
    cd build
    ninja
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
}
