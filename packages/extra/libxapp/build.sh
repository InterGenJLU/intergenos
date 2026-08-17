#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# libxapp 3.3.3 — the XApp utility library.
#
# app-lib-only limits the build to the library, its GSettings schema, its
# introspection data and its Vala binding. The rest of the upstream project
# (status-notifier service, MATE/XFCE applets, wallpaper scripts) targets
# desktop environments this distribution does not ship, and the keyboard
# layout controller it would otherwise build needs libgnomekbdui, which is
# not in the tree.
#
# The Vala binding is what timeshift compiles against, and generating it
# requires introspection — upstream asserts that pairing at configure time —
# so both stay enabled.

configure() {
    set -e
    mkdir -p build
    cd       build

    meson setup ..                  \
          --prefix=/usr             \
          --libdir=/usr/lib         \
          --buildtype=release       \
          --wrap-mode=nodownload    \
          -Dapp-lib-only=true       \
          -Dstatus-notifier=false   \
          -Dmate=false              \
          -Dxfce=false              \
          -Ddocs=false              \
          -Dintrospection=true      \
          -Dvapi=true               \
          -Dgtk-layer-shell=disabled
}

build() {
    set -e
    cd build
    ninja -j${IGOS_JOBS}
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
}

post_install() {
    set -e
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
    ldconfig 2>/dev/null || true
}
