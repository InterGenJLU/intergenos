#!/bin/bash
# appstream-glib 0.8.3 — AppStream metadata reading and writing library
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..              \
          --prefix=/usr         \
          --libdir=/usr/lib     \
          --buildtype=release   \
          -D rpm=false
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
}
