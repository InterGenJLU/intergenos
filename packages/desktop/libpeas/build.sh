#!/bin/bash
# libpeas 1.36.0 — GObject plugin system
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    sed "/docs_dir =/s@\$@/ 'libpeas-${PKG_VERSION}'@" -i ../docs/reference/meson.build
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          --wrap-mode=nofallback \
          -Ddemos=false \
          -Dgtk_doc=false \
          -Dpython3=false
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
