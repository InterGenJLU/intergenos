#!/bin/bash
# librsvg 2.59.2 — SVG rendering library
# BLFS 13.0 (adapted for version)

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
