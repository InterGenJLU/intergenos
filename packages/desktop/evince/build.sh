#!/bin/bash
# evince 48.1 — GNOME document viewer
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -D gtk_doc=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
