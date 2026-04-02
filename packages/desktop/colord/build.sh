#!/bin/bash
# colord 1.4.7 — Color management daemon
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --buildtype=release \
          -Ddocs=false \
          -Dman=false \
          -Ddaemon_user=colord
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
