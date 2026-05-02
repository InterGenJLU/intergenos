#!/bin/bash
# glib2 2.88.1 — GLib with introspection enabled
# Final pass of bootstrap: glib-bootstrap → gobject-introspection → glib (full)
# gobject-introspection is already installed, so this is a clean single-pass build.
# Note: glib 2.79+ moved libgirepository into glib (soname bumped 1.0 -> 2.0);
# the gobject-introspection package still provides g-ir-scanner / g-ir-compiler
# tools needed at build time. BLFS 13.0.

configure() {
    mkdir build
    cd    build

    meson setup ..                  \
          --prefix=/usr             \
          --libdir=/usr/lib         \
          --buildtype=release       \
          -D introspection=enabled  \
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
