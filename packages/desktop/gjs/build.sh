#!/bin/bash
# gjs 1.86.0 — GNOME JavaScript bindings
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dinstalled_tests=false \
          -Dskip_dbus_tests=true
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
