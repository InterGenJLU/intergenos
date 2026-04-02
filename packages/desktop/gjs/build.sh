#!/bin/bash
# gjs 1.82.1 — GNOME JavaScript bindings
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
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
