#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Grep 3.12
# LFS 13.0 Section 8.36

configure() {
    set -e
    # Suppress egrep/fgrep deprecation warning that breaks some test suites
    sed -i "s/echo/#echo/" src/egrep.sh

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
}
