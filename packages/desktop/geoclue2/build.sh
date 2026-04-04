#!/bin/bash
# geoclue2 2.7.2 — D-Bus geolocation service
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dgtk-doc=false \
          -D3g-source=false \
          -Dmodem-gps-source=false \
          -Dcdma-source=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
