#!/bin/bash
# libdvdread 7.0.1 — DVD reading library
# BLFS 13.0

configure() {
    # BLFS required fix
    sed -i "/get_option/s/libdvdread/&-7.0.1/" meson.build

    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
    rm -fv ${DESTDIR}/usr/lib/libdvdread.a
}
