#!/bin/bash
# libpeas 1.36.0 — GObject plugin system
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --buildtype=release \
          --wrap-mode=nofallback \
          -Ddemos=false \
          -Dgtk_doc=false \
          -Dpython3=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
