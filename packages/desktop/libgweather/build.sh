#!/bin/bash
# libgweather 4.4.4 — GNOME weather information library
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    sed "s/libgweather_full_version/'libgweather-${PKG_VERSION}'/" -i docs/meson.build
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dgtk_doc=false \
          -Dtests=false
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
