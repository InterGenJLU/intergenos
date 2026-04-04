#!/bin/bash
# gnome-online-accounts 3.56.4 — GNOME online accounts service
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Ddocumentation=false \
          -Dman=false \
          -Dkerberos=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
