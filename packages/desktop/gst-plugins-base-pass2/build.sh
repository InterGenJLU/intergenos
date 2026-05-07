#!/bin/bash
# gst-plugins-base 1.28.1 — pass 2 rebuild with Mesa GL support
# BLFS 13.0
#
# Pass 1 builds before Mesa (no GL available), so libgstgl-1.0.so
# is not produced. GTK4 requires gstreamer-gl-1.0 for media playback.
# This pass rebuilds after Mesa is installed, enabling GL auto-detection.

configure() {
    set -e
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dtests=disabled \
          -Dexamples=disabled \
          -Ddoc=disabled \
          --wrap-mode=nodownload
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
