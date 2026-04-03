#!/bin/bash
# json-glib 1.10.6 — JSON parser for GLib
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed "/json_docdir =/s|\$| / 'json-glib-${PKG_VERSION}'|" -i ../doc/meson.build
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --buildtype=release 
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
