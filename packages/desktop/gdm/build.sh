#!/bin/bash
# gdm 47.0 — GNOME Display Manager
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --buildtype=release \
          -Dgdm-xsession=true \
          -Drun-dir=/run/gdm \
          -Ddefault-pam-config=lfs
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
