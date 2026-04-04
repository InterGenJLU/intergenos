#!/bin/bash
# libinput 1.31.0 — Input device management and event handling library
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Ddebug-gui=false \
          -Dtests=false \
          -Ddocumentation=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
