#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Man-pages 6.17
# LFS 13.0 Section 8.3
#
# No configure or build step. Just removes conflicting pages and installs.

configure() {
    set -e
    # Remove crypt man pages — libxcrypt provides better versions
    rm -v man3/crypt*
}

build() {
    set -e
    : # Nothing to build
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" -R GIT=false prefix=/usr install
}
