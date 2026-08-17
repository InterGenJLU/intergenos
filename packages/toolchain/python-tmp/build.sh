#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Python 3.14.3 (temporary tools)
# LFS 13.0 Section 7.9

configure() {
    set -e
    ./configure --prefix=/usr           \
                --enable-shared         \
                --without-ensurepip     \
                --without-static-libpython
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

install() {
    set -e
    make DESTDIR=$IGOS install
}
