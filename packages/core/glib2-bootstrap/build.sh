#!/bin/bash
# glib2-bootstrap 2.88.1 — GLib without introspection
# First pass of bootstrap: glib-bootstrap → gobject-introspection → glib (full)
# BLFS 13.0

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..                  \
          --prefix=/usr             \
          --libdir=/usr/lib         \
          --buildtype=release       \
          -D introspection=disabled \
          -D glib_debug=disabled    \
          -D man-pages=disabled     \
          -D sysprof=disabled
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
