#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# libxslt 1.1.45 — XSLT processor library
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr --with-crypto \
                --disable-static \
                --without-python
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
