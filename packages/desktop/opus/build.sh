#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# opus 1.6.1 — Interactive speech and audio codec
# BLFS 13.0

configure() {
    set -e
    mkdir -p build &&
    cd    build &&

    meson setup --prefix=/usr        \
                --libdir=/usr/lib    \
                --buildtype=release  \
                -D docdir=/usr/share/doc/opus-1.6.1
}

build() {
    set -e
    cd build &&
    ninja
}

do_install() {
    set -e
    cd build &&
    DESTDIR="$DESTDIR" ninja install
}
