#!/bin/bash
# iso-codes 4.20.1 — Country, language, and currency code lists
# BLFS 13.0

configure() {
    set -e
    mkdir -p build
    cd    build
    meson setup .. --prefix=/usr --libdir=/usr/lib --buildtype=release
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
