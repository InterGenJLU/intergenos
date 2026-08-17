#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Libcap 2.77
# LFS 13.0 Section 8.27
#
# DESTDIR exception: needs prefix=, lib=, and RAISE_SETFCAP=no for staging.

configure() {
    set -e
    # Prevent static library installation
    sed -i '/install -m.*STA/d' libcap/Makefile
}

build() {
    set -e
    make prefix=/usr lib=lib -j${IGOS_JOBS}
}

check() {
    set -e
    make test
}

do_install() {
    set -e
    make prefix=/usr lib=lib DESTDIR="$DESTDIR" RAISE_SETFCAP=no install
}
