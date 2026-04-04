#!/bin/bash
# libnma 1.10.6 — NetworkManager GTK widgets
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dgtk_doc=false \
          -Dgtk4=true
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
