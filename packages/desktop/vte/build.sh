#!/bin/bash
# vte 0.78.2 — Virtual Terminal Emulator widget
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed -e "/docdir =/s@\$@/ 'vte-${PKG_VERSION}'@" -i ../doc/meson.build
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Da11y=true \
          -Dgtk3=true \
          -Dgtk4=true \
          -D_b_lto=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
