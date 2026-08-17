#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# libgpg-error 1.59 — GPG error code library
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr \
                --sysconfdir=/etc \
                --disable-static
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
