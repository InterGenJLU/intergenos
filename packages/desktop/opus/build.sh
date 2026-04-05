#!/bin/bash
# opus 1.6.1 — Interactive speech and audio codec
# BLFS 13.0

configure() {
    mkdir build &&
    cd    build &&

    meson setup --prefix=/usr        \
                --buildtype=release  \
                -D docdir=/usr/share/doc/opus-1.6.1
}

build() {
    cd build &&
    ninja
}

do_install() {
    cd build &&
    DESTDIR="$DESTDIR" ninja install
}
