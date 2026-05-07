#!/bin/bash
# gjs 1.86.0 — GNOME JavaScript bindings
# BLFS 13.0

configure() {
    set -e
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          --wrap-mode=nofallback \
          -Dinstalled_tests=false \
          -Dskip_dbus_tests=true
}

build() {
    set -e
    cd build
    ninja
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
}
