#!/bin/bash
# libdvdnav 7.0.0 — DVD navigation library
# BLFS 13.0

configure() {
    set -e
    # BLFS: install documentation in a versioned directory
    sed -i "/get_option/s/libdvdnav/&-7.0.0/" meson.build

    mkdir build
    cd    build

    meson setup --prefix=/usr --libdir=/usr/lib --buildtype=release ..
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

    # Remove static library if present
    rm -fv "$DESTDIR/usr/lib/libdvdnav.a"
}
