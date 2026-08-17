#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# hicolor-icon-theme 0.18 — Default fallback icon theme
# BLFS 13.0

configure() {
    set -e
    mkdir -p build
    cd    build
    meson setup .. --prefix=/usr --libdir=/usr/lib
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
    gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
}
