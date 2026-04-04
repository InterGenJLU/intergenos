#!/bin/bash
# xdg-user-dirs 0.19 — XDG user directory management
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dsysconfdir=/etc
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
