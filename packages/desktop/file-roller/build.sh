#!/bin/bash
# file-roller 44.6 — GNOME archive manager
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -D packagekit=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
    chmod -v 0755 ${DESTDIR}/usr/libexec/file-roller/isoinfo.sh 2>/dev/null || true
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
