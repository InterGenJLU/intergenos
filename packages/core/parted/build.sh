#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# parted 3.7 — partition manipulation (BLFS 13.0 / postlfs/parted).
# T0-3 sub-cluster 1 — installer runtime dep (installer/backend/disks.py).

configure() {
    set -e
    ./configure --prefix=/usr \
                --disable-static \
                --docdir=/usr/share/doc/parted-${version}
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
