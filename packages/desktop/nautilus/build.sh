#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# nautilus 49.3 — GNOME file manager
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    sed "/docdir =/s@\$@ / 'nautilus-${PKG_VERSION}'@" -i meson.build
    # gexiv2 0.16 renamed its pkg-config module gexiv2 -> gexiv2-0.16 (0.16.0
    # ships ONLY gexiv2-0.16.pc), but 49.3 still probes the retired name and
    # configure dies at meson.build:133. Backport upstream nautilus main's own
    # migration verbatim: dependency('gexiv2-0.16', version: '>= 0.16.0').
    sed -i "s/dependency('gexiv2', version: '>= 0.14.2')/dependency('gexiv2-0.16', version: '>= 0.16.0')/" meson.build
    grep -q "dependency('gexiv2-0.16'" meson.build || { echo "FATAL: gexiv2-0.16 probe migration did not land — upstream meson.build changed shape; re-derive the sed"; exit 1; }
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dtests=none \
          -Ddocs=false
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
