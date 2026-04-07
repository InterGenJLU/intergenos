#!/bin/bash
# geoclue2 2.8.0 — D-Bus geolocation service
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
          -Dcdma-source=false \
          -Dnmea-source=false
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
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
