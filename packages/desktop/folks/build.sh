#!/bin/bash
# folks 0.15.12 — People aggregation library
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    # Telepathy backend disabled: Telepathy is upstream-abandoned (BLFS 13.0 dropped
    # it entirely; folks meson.build:86 carries an upstream FIXME to drop dbus-glib).
    # Owner-approved 2026-05-03 per feedback_never_disable_features.md exception clause.
    meson setup ..                       \
          --prefix=/usr                  \
          --libdir=/usr/lib              \
          --buildtype=release            \
          -Dtelepathy_backend=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
