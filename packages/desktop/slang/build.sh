#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# slang 2.3.3 — S-Lang programming library
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr \
                --sysconfdir=/etc \
                --with-readline=gnu
}

build() {
    set -e
    # BLFS: slang does not support parallel build; RPATH= prevents hardcoded paths
    make -j1 RPATH=
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" RPATH= install
}
