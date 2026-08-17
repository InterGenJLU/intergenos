#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# libadwaita1 1.8.4 — GTK4 adaptive widgets library
# BLFS 13.0

configure() {
    set -e
    # InterGenOS brand: remap libadwaita's BLUE accent (#3584e4 -> #0099FF) in
    # the enum->rgba table (Settings picker swatch + active accent) AND the
    # --accent-blue SCSS custom property. patch fails LOUD if upstream moves
    # either line — no silent no-op. The other 8 GNOME accents stay stock.
    BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
    patch -p1 < "$BUILD_DIR/accent-blue-brand.patch"

    # BLFS required fixes
    sed "s/apiversion/'${PKG_VERSION}'/" -i doc/meson.build
    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release
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
}
