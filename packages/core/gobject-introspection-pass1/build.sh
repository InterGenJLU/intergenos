#!/bin/bash
# gobject-introspection-pass1 1.86.0 — GObject typeinfo (bootstrap)
# First pass of 2-pass build. Disables cairo + doctool (both tier:desktop
# deps) so glib2 (tier:core) can build with introspection without pulling
# the GUI stack into tier:core. Full gobject-introspection with cairo +
# doctool lives in tier:desktop and supersedes pass1 via
# migrate-pkm-supersedes.sh.

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dcairo=disabled    \
          -Ddoctool=disabled
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
