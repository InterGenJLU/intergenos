#!/bin/bash
# fuse3 3.18.1 — Filesystem in Userspace
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed -i '/^udev/,$ s/^/#/' util/meson.build
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dexamples=false \
          -Duseroot=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
