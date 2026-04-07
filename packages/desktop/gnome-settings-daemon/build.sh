#!/bin/bash
# gnome-settings-daemon 49.1 — GNOME settings daemon
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
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
    update-desktop-database /usr/share/applications 2>/dev/null || true
}
