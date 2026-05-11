#!/bin/bash
# glib-networking 2.80.1 — GIO networking extensions
# BLFS 13.0

configure() {
    set -e
    mkdir -p build
    cd    build

    # libproxy enables system-wide PAC/proxy autodetect. Build #5 audit
    # found this disabled and the libproxy package was in the wrong tier
    # (extra, built after desktop). libproxy moved to desktop tier in the
    # same batch; flip flag to enabled.
    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dlibproxy=enabled
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

post_install() {
    set -e
    gio-querymodules /usr/lib/gio/modules
}
