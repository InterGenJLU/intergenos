#!/bin/bash
# pipewire 1.2.7 — Multimedia processing framework
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dsession-managers=[] \
          -Dtests=disabled \
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
