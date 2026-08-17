#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# gvfs 1.58.2 — GNOME virtual filesystem
# BLFS 13.0
#
# First pass: builds before gnome-online-accounts.
# GOA and OneDrive backends are disabled here and enabled in gvfs-pass2.
# Google backend disabled permanently (libgdata deprecated, approved).

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dman=false \
          -Dgoogle=false \
          -Dgoa=false \
          -Donedrive=false
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

post_install() {
    set -e
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
    gio-querymodules /usr/lib/gio/modules 2>/dev/null || true
}
