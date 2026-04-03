#!/bin/bash
# glib2-bootstrap 2.86.4 — GLib without introspection
# First pass of bootstrap: glib-bootstrap → gobject-introspection → glib (full)
# BLFS 13.0

configure() {
    patch -Np1 -i "${IGOS_SOURCES}/glib-2.86.4-upstream_fixes-1.patch"

    mkdir build
    cd    build

    meson setup ..                  \
          --prefix=/usr             \
          --buildtype=release       \
          -D introspection=disabled \
          -D glib_debug=disabled    \
          -D man-pages=disabled     \
          -D sysprof=disabled
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
