#!/bin/bash
# libproxy 0.5.12 — Automatic proxy configuration management library
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          -D release=true     \
          -D docs=false
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

    # Remove useless static library per BLFS
    rm -f "$DESTDIR"/usr/lib/libproxy.a
}
