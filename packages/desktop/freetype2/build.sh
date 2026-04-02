#!/bin/bash
# freetype2 2.13.3 — FreeType font rendering library (pass 2 — with HarfBuzz)
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --buildtype=release \
          -Dharfbuzz=enabled \
          -Dbrotli=enabled \
          -Dpng=enabled
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
