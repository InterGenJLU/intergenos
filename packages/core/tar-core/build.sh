#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Tar 1.35
# LFS 13.0 Section 8.73

configure() {
    set -e
    FORCE_UNSAFE_CONFIGURE=1 \
    ./configure --prefix=/usr
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    make check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
    make DESTDIR="$DESTDIR" -C doc install-html docdir=/usr/share/doc/tar-1.35
}
