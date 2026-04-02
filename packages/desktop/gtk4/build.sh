#!/bin/bash
# gtk4 4.16.12 — GTK 4 widget toolkit
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --buildtype=release \
          -Dbroadway-backend=true \
          -Dintrospection=enabled \
          -Dbuild-testsuite=false \
          -Dbuild-tests=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
