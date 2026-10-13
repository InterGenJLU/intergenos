#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# libarchive 3.8.6 — Multi-format archive library
# BLFS 13.0

configure() {
    set -e
    # --without-xml2: the lineage built libarchive before libxml2 existed in the
    # build root, so its XML support is expat's; on a populated root configure
    # finds libxml2, prefers it, and the archive gains a libxml2.so.2 dependency
    # the lineage never had. State the lineage's choice instead of leaving it to
    # the root's contents.
    ./configure --prefix=/usr --disable-static --without-xml2
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
    # No /usr/bin/unzip link to bsdunzip: Info-ZIP unzip (packages/base/unzip)
    # ships its program at /usr/bin/unzip as a regular file, and two packages
    # cannot ship one path as two kinds of file — the second install wrote
    # Info-ZIP's unzip through this link into /usr/bin/bsdunzip, and pkm now
    # refuses it. libarchive's tool stays reachable as bsdunzip.
}
