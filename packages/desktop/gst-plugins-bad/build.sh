#!/bin/bash
# gst-plugins-bad 1.24.10 — GStreamer bad plugins
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dtests=disabled \
          -Dexamples=disabled \
          -Ddoc=disabled
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
