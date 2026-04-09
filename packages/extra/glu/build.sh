#!/bin/bash
# glu 9.0.3 — Mesa OpenGL Utility library
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..              \
          --prefix=/usr         \
          --buildtype=release   \
          -D gl_provider=gl     \
          -D default_library=shared
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
