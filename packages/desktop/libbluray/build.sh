#!/bin/bash
# libbluray 1.4.1 — Blu-ray disc playback library
# Not in BLFS — uses meson (switched from autotools in recent versions)

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dbdj_jar=disabled
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
