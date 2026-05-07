#!/bin/bash
# freetype2 2.14.1 — FreeType font rendering library (pass 2 — with HarfBuzz)
# BLFS 13.0

configure() {
    set -e
    # Enable GX/AAT and OpenType table validation
    sed -ri "s:.*(AUX_MODULES.*valid):\1:" modules.cfg

    # Enable subpixel rendering (improves font clarity on LCD screens)
    sed -r "s:.*(#.*SUBPIXEL_RENDERING) .*:\1:" \
        -i include/freetype/config/ftoption.h

    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dharfbuzz=enabled \
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
