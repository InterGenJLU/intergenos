#!/bin/bash
# libadwaita1 1.8.4 — GTK4 adaptive widgets library
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed "s/apiversion/'${PKG_VERSION}'/" -i ../doc/meson.build
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dexamples=false \
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
