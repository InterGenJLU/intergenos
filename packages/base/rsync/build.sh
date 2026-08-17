#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# rsync 3.4.1 — Fast incremental file transfer
# BLFS 13.0

configure() {
    set -e
    # Patch applied by builder PATCH phase (package.yml) with SHA256 validation.

    ./configure --prefix=/usr    \
                --without-included-zlib
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    sed -i '/typedef/d' wildtest.c
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        make check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
