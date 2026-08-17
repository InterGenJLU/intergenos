#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# libnsl 2.0.1 — NIS library (libnsl replacement for glibc)
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr     \
                --sysconfdir=/etc \
                --disable-static
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
}
