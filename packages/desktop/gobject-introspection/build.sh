#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# gobject-introspection 1.86.0 — GObject typeinfo (full with cairo)
# Pass 2 of the 2-pass build. Rebuilds with -Dcairo=enabled and
# -Ddoctool=enabled now that cairo is available in tier:desktop.
# Supersedes the pass 1 build at install time.

configure() {
    set -e
    mkdir -p build
    cd build
    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dcairo=enabled     \
          -Ddoctool=enabled
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
