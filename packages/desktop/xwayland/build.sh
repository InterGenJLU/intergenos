#!/bin/bash
# xwayland 24.1.5 — X server running as a Wayland client
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --buildtype=release \
          -Dxkb_dir=/usr/share/X11/xkb \
          -Dxkb_output_dir=/var/lib/xkb
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
