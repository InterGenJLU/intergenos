#!/bin/bash
# appstream-glib 0.8.3 — AppStream metadata reading and writing library
# BLFS 13.0

configure() {
    set -e
    mkdir build
    cd    build

    meson setup ..              \
          --prefix=/usr         \
          --libdir=/usr/lib     \
          --buildtype=release   \
          -D rpm=false
}

build() {
    set -e
    cd build
    ninja
}

check() {
    set -e
    cd build
    ninja test || true
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
}
