#!/bin/bash
# geoclue2 2.8.0 — D-Bus geolocation service
# BLFS 13.0

configure() {
    set -e
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dgtk-doc=false \
          -D3g-source=false \
          -Dmodem-gps-source=false \
          -Dcdma-source=false \
          -Dnmea-source=false
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

post_install() {
    set -e
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
