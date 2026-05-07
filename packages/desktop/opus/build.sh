#!/bin/bash
# opus 1.6.1 — Interactive speech and audio codec
# BLFS 13.0

configure() {
    set -e
    mkdir build &&
    cd    build &&

    meson setup --prefix=/usr        \
                --libdir=/usr/lib    \
                --buildtype=release  \
                -D docdir=/usr/share/doc/opus-1.6.1
}

build() {
    set -e
    cd build &&
    ninja
}

do_install() {
    set -e
    cd build &&
    DESTDIR="$DESTDIR" ninja install
}
