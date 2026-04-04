#!/bin/bash
# libhandy 1.8.3 — GTK3 adaptive widget library
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..              \
          --prefix=/usr         \
          --libdir=/usr/lib     \
          --buildtype=release
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
