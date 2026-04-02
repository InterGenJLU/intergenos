#!/bin/bash
# mutter 47.4 — GNOME window manager and Wayland compositor
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --buildtype=release \
          -Dtests=disabled \
          -Ddocs=false \
          -Dprofiler=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
