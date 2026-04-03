#!/bin/bash
# gdk-pixbuf 2.44.5 — Image loading library for GTK
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed "/docs_dir =/s@\$@ / 'gdk-pixbuf-${PKG_VERSION}'@" -i ../docs/meson.build
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --buildtype=release \
          -Dman=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}

post_install() {
    gdk-pixbuf-query-loaders --update-cache
}
