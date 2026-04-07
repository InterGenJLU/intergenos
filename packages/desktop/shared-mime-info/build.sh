#!/bin/bash
# shared-mime-info 2.4 — Core MIME type database
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release 
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}

post_install() {
    update-mime-database /usr/share/mime 2>/dev/null || true
}
