#!/bin/bash
# libgweather 4.4.4 — GNOME weather information library
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    # Path is doc/ singular in libgweather 4.4.4 (verified against tarball);
    # earlier sweep at halt #16 fixed ../docs → docs but didn't catch the
    # docs/doc rename. Confirmed via tar tf: libgweather-4.4.4/doc/meson.build.
    sed "s/libgweather_full_version/'libgweather-${PKG_VERSION}'/" -i doc/meson.build
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dgtk_doc=false \
          -Dtests=false
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
