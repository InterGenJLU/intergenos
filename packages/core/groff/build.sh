#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Groff 1.23.0
# LFS 13.0 Section 8.65
#
# CRITICAL: PAGE must be set at configure time.
# letter = US, A4 = everywhere else.

configure() {
    set -e
    PAGE=letter ./configure --prefix=/usr
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
}
