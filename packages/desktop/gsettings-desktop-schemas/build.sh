#!/bin/bash
# gsettings-desktop-schemas 49.1 — GSettings schemas for GNOME desktop
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed -i -r 's:"(/system):"/org/gnome\1:g' schemas/*.in
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release 
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
