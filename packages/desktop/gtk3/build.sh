#!/bin/bash
# gtk3 3.24.51 — GTK 3 widget toolkit
# BLFS 13.0

configure() {
    set -e
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dman=true \
          -Dbroadway_backend=true
}

build() {
    set -e
    cd build
    ninja
}

check() {
    set -e
    cd build
    # Requires graphical session per BLFS; failures expected in chroot
    ninja test || true
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
}

post_install() {
    set -e
    gtk-query-immodules-3.0 --update-cache
    glib-compile-schemas /usr/share/glib-2.0/schemas
}
