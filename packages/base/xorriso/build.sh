#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# xorriso 1.5.8.pl02 — ISO 9660 / Rock Ridge filesystem image manipulation
# BLFS-style autotools build. libisoburn/libisofs/libburn are bundled in the
# tarball and built in-tree — no external lib needed for them. zlib and
# readline are the real external link dependencies (both enabled by default).

configure() {
    set -e
    ./configure --prefix=/usr
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
