#!/bin/bash
# nautilus 49.3 — GNOME file manager
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    sed "/docdir =/s@\$@ / 'nautilus-${PKG_VERSION}'@" -i meson.build
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dtests=none \
          -Ddocs=false
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
