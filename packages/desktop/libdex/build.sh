#!/bin/bash
# libdex 1.1.0 — Deferred Execution / async-await primitives for GLib
# https://gitlab.gnome.org/GNOME/libdex

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dexamples=false    \
          -Dtests=false       \
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
