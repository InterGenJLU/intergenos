#!/bin/bash
# dbus-python 1.4.0 — Python bindings for D-Bus
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..              \
          --prefix=/usr         \
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
