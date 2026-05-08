#!/bin/bash
# libshumate 1.5.3 — GTK4 map widget
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    sed -e 's/lib_version/version/' -i docs/meson.build
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dgtk_doc=false
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
