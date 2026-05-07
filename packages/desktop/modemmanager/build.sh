#!/bin/bash
# modemmanager 1.24.2 — Mobile broadband modem management daemon
# BLFS 13.0

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dgtk_doc=false \
          -Dman=false \
          -Dtests=false \
          -Dbash_completion=false \
          -Dqrtr=false
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
