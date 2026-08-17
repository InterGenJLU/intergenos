#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# duktape 2.7.0 — Embeddable JavaScript engine
# BLFS 13.0

configure() {
    set -e
    sed -i 's/-Os/-O2/' Makefile.sharedlibrary
}

build() {
    set -e
    make -j${IGOS_JOBS} -f Makefile.sharedlibrary INSTALL_PREFIX=/usr
}

do_install() {
    set -e
    make -f Makefile.sharedlibrary INSTALL_PREFIX=/usr DESTDIR="$DESTDIR" install
}
