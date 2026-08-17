#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# gc 8.2.12 — Boehm-Demers-Weiser conservative garbage collector
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr      \
                --enable-cplusplus \
                --disable-static   \
                --docdir=/usr/share/doc/gc-8.2.12
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        make check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # BLFS: install man page
    install -v -m644 doc/gc.man "$DESTDIR/usr/share/man/man3/gc_malloc.3"
}
