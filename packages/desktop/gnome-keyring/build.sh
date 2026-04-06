#!/bin/bash
# gnome-keyring 48.0 — GNOME password and secret storage
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed -i 's:"/desktop:"/org:' schema/*.xml

    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -D selinux=disabled
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
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
