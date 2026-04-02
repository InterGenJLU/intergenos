#!/bin/bash
# polkit 125 — PolicyKit authorization toolkit
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --buildtype=release \
          -Dtests=false \
          -Dman=false \
          -Djs_engine=duktape
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
