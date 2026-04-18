#!/bin/bash
# libspelling 0.4.10 — Spellcheck library for GTK 4
# BLFS 13.0

configure() {
    # meson 1.10+ disallows add_global_arguments after build targets
    sed -i 's/  add_global_arguments/  # add_global_arguments/' meson.build

    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Ddocs=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
