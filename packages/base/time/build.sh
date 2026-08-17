#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Time 1.9 — GNU time command
# BLFS 13.0

configure() {
    set -e
    # GCC-15 fix
    sed -i 's/sighandler interrupt_signal/__sighandler_t interrupt_signal/' src/time.c

    ./configure --prefix=/usr
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
