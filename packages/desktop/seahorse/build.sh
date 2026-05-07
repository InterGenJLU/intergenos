#!/bin/bash
# seahorse 47.0.1 — GNOME password and encryption key manager
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    sed -i "/GPGME_EVENT_NEXT_TRUSTITEM/d" pgp/seahorse-gpgme.c
    sed -i -r 's:"(/apps):"/org/gnome\1:' data/*.xml

    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release
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
