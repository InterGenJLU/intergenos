#!/bin/bash
# gobject-introspection 1.86.0 — GObject type introspection
# Builds against glib2-bootstrap, provides g-ir-scanner for full glib2 build
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
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
