#!/bin/bash
# xdg-desktop-portal-gnome 49.0 — GNOME backend for xdg-desktop-portal
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

post_install() {
    glib-compile-schemas /usr/share/glib-2.0/schemas
}
