#!/bin/bash
# libgusb 0.4.9 — GObject wrapper for libusb
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    sed -E "/output|install_dir/s/('libgusb)'/\1-${PKG_VERSION}'/" -i ../docs/meson.build
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Ddocs=false \
          -Dtests=false
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
