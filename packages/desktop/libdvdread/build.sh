#!/bin/bash
# libdvdread 7.0.1 — DVD reading library
# BLFS 13.0

configure() {
    set -e
    # BLFS required fix
    sed -i "/get_option/s/libdvdread/&-7.0.1/" meson.build

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
    rm -fv ${DESTDIR}/usr/lib/libdvdread.a
}
