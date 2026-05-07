#!/bin/bash
# gnome-terminal 3.58.1 — GNOME terminal emulator
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    sed -i -r 's:"(/system):"/org/gnome\1:g' src/external.gschema.xml
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release 
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

post_install() {
    set -e
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
