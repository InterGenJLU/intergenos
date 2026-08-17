#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# osinfo-db-tools 1.12.0 — osinfo database import/export tools
# Not in BLFS — InterGenOS extra tier (virtualization stack)
#
# osinfo-db-import/-export/-validate/-path. Build-time dependency of
# the osinfo-db package (its install step runs osinfo-db-import) and
# the runtime updater for the database.

configure() {
    set -e
    meson setup build \
        --prefix=/usr \
        --libdir=/usr/lib \
        --buildtype=release
}

build() {
    set -e
    ninja -C build
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" ninja -C build install
}
