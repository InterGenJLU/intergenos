#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# lmdb 0.9.35 — Lightning Memory-Mapped Database
# BLFS 13.0

configure() {
    set -e
    cd libraries/liblmdb
}

build() {
    set -e
    cd libraries/liblmdb
    make
}

do_install() {
    set -e
    cd libraries/liblmdb

    # Remove static library from install target per BLFS
    sed -i 's| liblmdb.a||' Makefile

    make prefix=/usr DESTDIR="$DESTDIR" install
}
