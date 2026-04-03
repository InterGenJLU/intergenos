#!/bin/bash
# libsecret 0.21.4 — Library for accessing secrets stored in the keyring
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed "s/api_version_major/'${PKG_VERSION}'/" -i ../docs/reference/libsecret/meson.build
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --buildtype=release \
          -Dgtk_doc=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
