#!/bin/bash
# gnome-screenshot 41.0 — GNOME screenshot utility
# BLFS 13.0

configure() {
    set -e
    # BLFS required fix
    sed -i '/merge_file/{n;d}' data/meson.build

    mkdir build
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
