#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# librest 0.10.2 — REST web service access library
# BLFS 13.0

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dgtk_doc=false
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

    # Decided 2026-07-17: the REST demo launcher ships for developer use,
    # not as an end-user app — hide it from the app menu (NoDisplay=true).
    # test -f fails loudly if upstream moves/renames it.
    f="$DESTDIR/usr/share/applications/org.gnome.RestDemo.desktop"
    test -f "$f"
    sed -i '/^NoDisplay=/d' "$f"
    sed -i '/^\[Desktop Entry\]/a NoDisplay=true' "$f"
}
