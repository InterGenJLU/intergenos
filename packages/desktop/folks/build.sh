#!/bin/bash
# folks 0.15.12 — People aggregation library
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    # Telepathy backend disabled: Telepathy is upstream-abandoned (BLFS 13.0 dropped
    # it entirely; folks meson.build:86 carries an upstream FIXME to drop dbus-glib).
    # Owner-approved 2026-05-03 as policy exception (telepathy backend is upstream-abandoned).
    #
    # Tests disabled: meson.build:117 hard-requires python-dbusmock for the BlueZ
    # test suite. python-dbusmock is a tests-only dep — falls in the
    # "Optional — docs/tests only — SKIP" category per the dependency-enablement
    # policy. Tests don't ship; no user-facing functionality lost. Owner-approved
    # 2026-05-03.
    meson setup ..                       \
          --prefix=/usr                  \
          --libdir=/usr/lib              \
          --buildtype=release            \
          -Dtelepathy_backend=false      \
          -Dtests=false
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
