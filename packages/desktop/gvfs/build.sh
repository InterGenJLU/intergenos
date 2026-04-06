#!/bin/bash
# gvfs 1.58.2 — GNOME virtual filesystem
# BLFS 13.0
#
# All backends enabled except:
#   -Dgoogle=false — libgdata deprecated by Google, removed from BLFS (owner approved)

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dman=false \
          -Dgoogle=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}

post_install() {
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
    gio-querymodules /usr/lib/gio/modules 2>/dev/null || true
}
