#!/bin/bash
# libinput 1.31.0 — Input device management and event handling library
# BLFS 13.0

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dudev-dir=/usr/lib/udev \
          -Ddebug-gui=false \
          -Dtests=false \
          -Ddocumentation=false
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
