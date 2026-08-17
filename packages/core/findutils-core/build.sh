#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Findutils 4.10.0
# LFS 13.0 Section 8.64

configure() {
    set -e
    ./configure --prefix=/usr \
        --localstatedir=/var/lib/locate
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    chown -R tester .
    su tester -c "PATH=$PATH make check"
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
