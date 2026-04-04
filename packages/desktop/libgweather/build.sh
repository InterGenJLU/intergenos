#!/bin/bash
# libgweather 4.4.4 — GNOME weather information library
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed "s/libgweather_full_version/'libgweather-${PKG_VERSION}'/" -i ../docs/meson.build
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dgtk_doc=false \
          -Dtests=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
