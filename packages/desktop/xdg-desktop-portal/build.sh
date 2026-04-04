#!/bin/bash
# xdg-desktop-portal 1.20.3 — Desktop integration portal
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Ddocbook-docs=disabled \
          -Dman-pages=disabled
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
