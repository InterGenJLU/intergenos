#!/bin/bash
# gtk4 4.20.3 — GTK 4 widget toolkit
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    sed "s@'doc'@& / 'gtk-${PKG_VERSION}'@" -i docs/reference/meson.build
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          --wrap-mode=nofallback \
          -Dbroadway-backend=true \
          -Dintrospection=enabled \
          -Dvulkan=enabled
}

build() {
    set -e
    cd build
    ninja
}

check() {
    set -e
    cd build
    # Requires graphical session per BLFS; failures expected in chroot
    ninja test || true
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
}

post_install() {
    set -e
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
