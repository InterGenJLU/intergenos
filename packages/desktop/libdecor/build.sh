#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# libdecor 0.2.5 — client-side window decorations for Wayland clients.
# The GTK plugin is disabled to keep the surface minimal; the cairo plugin
# (built from the in-tree cairo + pango) provides decorations at runtime.

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Ddemo=false        \
          -Dinstall_demo=false \
          -Dgtk=disabled      \
          -Ddbus=enabled
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
