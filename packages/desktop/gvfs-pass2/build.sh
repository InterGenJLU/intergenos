#!/bin/bash
# gvfs 1.58.2 — pass 2 rebuild with GOA and OneDrive support
# BLFS 13.0
#
# Pass 1 builds before gnome-online-accounts (no GOA/OneDrive).
# This pass rebuilds after GOA is available, enabling cloud backends.
#
# Only -Dgoogle=false remains (libgdata deprecated, owner approved).

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
