#!/bin/bash
# xwayland 24.1.9 — X server running as a Wayland client
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    # NOTE: original BLFS-style had \$ (escaped for double quotes). Inside
    # single quotes \$ is literal — sed sees unterminated address regex.
    # Use bare $ for sed's "to end of file" address.
    sed -i '/install_man/,$d' meson.build
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dxkb_output_dir=/var/lib/xkb
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
