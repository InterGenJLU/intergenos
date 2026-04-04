#!/bin/bash
# libxml2 2.15.1 — XML parsing library
# BLFS 13.0 (meson build with Python bindings)

configure() {
    # BLFS: remove unnecessary git call
    sed -i "/'git'/,+3d" meson.build

    mkdir build
    cd    build

    meson setup ..              \
          --prefix=/usr         \
          -Dhistory=enabled     \
          -Dicu=enabled         \
          -Dpython=enabled
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
