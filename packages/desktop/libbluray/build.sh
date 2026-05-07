#!/bin/bash
# libbluray 1.4.1 — Blu-ray disc playback library
# Not in BLFS — uses meson (switched from autotools in recent versions)

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dbdj_jar=disabled
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
