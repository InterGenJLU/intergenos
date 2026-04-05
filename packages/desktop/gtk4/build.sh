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
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dbroadway-backend=true \
          -Dintrospection=enabled \
          -Dvulkan=enabled
}

build() {
    cd build
    ninja
}

check() {
    cd build
    # Requires graphical session per BLFS; failures expected in chroot
    ninja test || true
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
