#!/bin/bash
# geocode-glib 3.26.4 — Geocoding and reverse geocoding library
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Denable-gtk-doc=false \
          -Dsoup2=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
