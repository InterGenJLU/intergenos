#!/bin/bash
# vte 0.78.2 — Virtual Terminal Emulator widget
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
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
