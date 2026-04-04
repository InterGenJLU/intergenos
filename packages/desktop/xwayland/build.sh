#!/bin/bash
# xwayland 24.1.5 — X server running as a Wayland client
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed -i '/install_man/,\$d' meson.build
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
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
