#!/bin/bash
# gvfs 1.58.2 — GNOME virtual filesystem
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dman=false \
          -Dgoogle=false \
          -Dgphoto2=false \
          -Dafc=false \
          -Dmtp=false \
          -Dnfs=false \
          -Ddnssd=false \
          -Dgoa=false \
          -Dbluray=false \
          -Dsmb=false \
          -Donedrive=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
