#!/bin/bash
# gst-plugins-ugly 1.28.1 — GStreamer ugly plugins (MP3 + patent-encumbered codecs)
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dtests=disabled \
          -Ddoc=disabled \
          -Dgpl=enabled
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
