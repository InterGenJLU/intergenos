#!/bin/bash
# gtk4 4.20.3 — GTK 4 widget toolkit
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed "s@'doc'@& / 'gtk-${PKG_VERSION}'@" -i ../docs/reference/meson.build
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --buildtype=release \
          -Dbroadway-backend=true \
          -Dintrospection=enabled \
          -Dvulkan=enabled \
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
