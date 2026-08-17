#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# hwdata 0.404 — Hardware identification and configuration data
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr \
                --disable-blacklist
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
