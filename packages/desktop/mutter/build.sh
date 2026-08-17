#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# mutter 49.4 — GNOME window manager and Wayland compositor
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    sed "/tests_c_args =/s/\$/ + ['-U', 'G_DISABLE_ASSERT']/" -i src/tests/meson.build
    sed "/c_args:/a '-U', 'G_DISABLE_ASSERT'," -i src/tests/cogl/unit/meson.build
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dtests=disabled \
          -Ddocs=false \
          -Dprofiler=false
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
