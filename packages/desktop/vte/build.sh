#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# vte 0.82.3 — Virtual Terminal Emulator widget
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    # Path is doc/reference/meson.build in vte 0.82.3 (verified via grep
     # for 'docdir =' in source). BLFS instruction was for a layout where
    # docdir lived in doc/meson.build directly.
    sed -e "/docdir =/s@\$@/ 'vte-${PKG_VERSION}'@" -i doc/reference/meson.build
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Da11y=true \
          -Dgtk3=true \
          -Dgtk4=true \
          -Db_lto=false
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

    # Decided 2026-07-17: the terminal-widget demo launchers ship for
    # developer use, not as end-user apps — hide them from the app menu
    # (NoDisplay=true). test -f fails loudly if upstream moves/renames one.
    for demo in org.gnome.Vte.App.Gtk3 org.gnome.Vte.App.Gtk4; do
        f="$DESTDIR/usr/share/applications/$demo.desktop"
        test -f "$f"
        sed -i '/^NoDisplay=/d' "$f"
        sed -i '/^\[Desktop Entry\]/a NoDisplay=true' "$f"
    done

    # Do not ship the profile.d files the retired hook deleted on every
    # target (hook-contract wave; negative payload removed at staging).
    rm -f "${DESTDIR}"/etc/profile.d/vte.*
}

