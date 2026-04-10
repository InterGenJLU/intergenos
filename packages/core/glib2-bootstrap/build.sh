#!/bin/bash
# glib2-bootstrap 2.86.4 — GLib without introspection
# First pass of bootstrap: glib-bootstrap → gobject-introspection → glib (full)
# BLFS 13.0

configure() {
    # Patch applied by builder PATCH phase (package.yml) with SHA256 validation.

    mkdir build
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
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
