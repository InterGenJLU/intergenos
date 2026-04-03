#!/bin/bash
# nautilus 47.2 — GNOME file manager
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed "/docdir =/s@\$@ / 'nautilus-${PKG_VERSION}'@" -i ../meson.build
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --buildtype=release \
          -Dtests=none \
          -Ddocs=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
