#!/bin/bash
# freetype2-pass1 2.13.3 — FreeType font rendering library (pass 1 — without HarfBuzz)
# BLFS 13.0

configure() {
    set -e
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dharfbuzz=disabled \
          -Dbrotli=enabled \
          -Dpng=enabled
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
