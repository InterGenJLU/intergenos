#!/bin/bash
# gvfs 1.58.2 — GNOME virtual filesystem
# BLFS 13.0
#
# First pass: builds before gnome-online-accounts.
# GOA and OneDrive backends are disabled here and enabled in gvfs-pass2.
# Google backend disabled permanently (libgdata deprecated, owner approved).

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dman=false \
          -Dgoogle=false \
          -Dgoa=false \
          -Donedrive=false
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
