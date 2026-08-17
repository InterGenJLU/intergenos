#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# libpwquality 1.4.5 — Password quality checking library
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr \
                --disable-static \
                --with-securedir=/usr/lib/security
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
