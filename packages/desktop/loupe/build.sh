#!/bin/bash
# loupe 49.2 — GNOME image viewer
# BLFS 13.0

configure() {
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
    rm -fv ${DESTDIR}/usr/share/applications/org.gnome.eog.desktop 2>/dev/null || true
}
