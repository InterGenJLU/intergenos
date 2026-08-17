#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# protobuf-c 1.5.2 — C implementation of Protocol Buffers
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    set -e
    make -j1
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
