#!/bin/bash
# libshumate 1.5.3 — GTK4 map widget
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed -e 's/lib_version/version/' -i ../docs/meson.build
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dgtk_doc=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
