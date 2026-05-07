#!/bin/bash
# vte 0.82.3 — Virtual Terminal Emulator widget
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    sed -e "/docdir =/s@\$@/ 'vte-${PKG_VERSION}'@" -i doc/meson.build
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Da11y=true \
          -Dgtk3=true \
          -Dgtk4=true \
          -Db_lto=false
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
    rm -fv /etc/profile.d/vte.*
}
