#!/bin/bash
# libsigcpp3 3.6.0 — Typesafe callback system for C++
# BLFS 13.0

configure() {
    # BLFS required fix
    sed -i "s/'system',//" meson.build

    mkdir bld
    cd    bld

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release
}

build() {
    cd bld
    ninja
}

do_install() {
    cd bld
    DESTDIR="$DESTDIR" ninja install
}
