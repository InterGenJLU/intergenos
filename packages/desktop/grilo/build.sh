#!/bin/bash
# grilo 0.3.19 — Media discovery framework
# BLFS 13.0

configure() {
    set -e
    mkdir -p build
    cd    build

    # -Denable-gtk-doc=false: project-wide convention (~25 GNOME packages
    # do the same). gtk-doc is not in our packages tree; we don't ship
    # API documentation to end users.
    meson setup ..              \
          --prefix=/usr         \
          --libdir=/usr/lib     \
          --buildtype=release   \
          -Denable-gtk-doc=false
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
