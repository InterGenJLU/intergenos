#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# gcr4 4.4.0.1 — GNOME crypto and certificate library
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    sed -e "/install_dir/s@,\$@ / 'gcr-${PKG_VERSION}'&@" -i docs/*/meson.build
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dgtk_doc=false \
          -Dssh_agent=true
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
