#!/bin/bash
# gnome-terminal 3.54.2 — GNOME terminal emulator
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed -i -r 's:"(/system):"/org/gnome\1:g' src/external.gschema.xml
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
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
