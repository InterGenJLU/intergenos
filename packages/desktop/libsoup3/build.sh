#!/bin/bash
# libsoup3 3.6.6 — HTTP client/server library for GNOME
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed 's/apiversion/soup_version/' -i docs/reference/meson.build
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          --wrap-mode=nofallback \
          -Dtests=false \
          -Ddocs=disabled
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
