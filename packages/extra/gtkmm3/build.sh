#!/bin/bash
# gtkmm3 3.24.10 — C++ interface to GTK3
# BLFS 13.0

configure() {
    set -e
    # BLFS uses "gtkmm3-build" to distinguish from the GTK4 version
    mkdir -p gtkmm3-build
    cd    gtkmm3-build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release
}

build() {
    set -e
    cd gtkmm3-build
    ninja
}

do_install() {
    set -e
    cd gtkmm3-build
    DESTDIR="$DESTDIR" ninja install
}
