#!/bin/bash
# libei 1.3.0 — Emulated Input library
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --buildtype=release \
          -Dtests=disabled \
          -Ddocumentation=[] \
          -Dman=disabled
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
