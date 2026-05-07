#!/bin/bash
# libsecret 0.21.7 — Library for accessing secrets stored in the keyring
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    sed "s/api_version_major/'${PKG_VERSION}'/" -i ../docs/reference/libsecret/meson.build
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dgtk_doc=false
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

post_install() {
    set -e
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
