#!/bin/bash
# hicolor-icon-theme 0.18 — Default fallback icon theme
# BLFS 13.0

configure() {
    mkdir build
    cd    build
    meson setup .. --prefix=/usr
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
