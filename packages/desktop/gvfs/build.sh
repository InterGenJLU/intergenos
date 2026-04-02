#!/bin/bash
# gvfs 1.54.2 — GNOME virtual filesystem
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --buildtype=release \
          -Dman=false \
          -Dgoogle=false \
          -Dgphoto2=false \
          -Dafc=false \
          -Dmtp=false \
          -Dnfs=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
