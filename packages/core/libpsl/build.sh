#!/bin/bash
# libpsl 0.21.5 — Public Suffix List library
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup --prefix=/usr --buildtype=release ..
}

build() {
    cd build
    ninja
}

check() {
    cd build
    ninja test || true
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
