#!/bin/bash
# json-glib 1.10.8 — JSON parser for GLib
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    sed "/json_docdir =/s|\$| / 'json-glib-${PKG_VERSION}'|" -i ../doc/meson.build
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release 
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
