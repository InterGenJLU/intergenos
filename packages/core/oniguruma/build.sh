#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# oniguruma 6.9.10 — multi-charset regular-expression library.
# Shipped as jq's regex engine (jq links the system libonig rather than its
# bundled submodule copy). Plain BLFS-style autotools build.

configure() {
    set -e
    ./configure --prefix=/usr --disable-static
}

build() {
    set -e
    make -j"${IGOS_JOBS:-$(nproc)}"
}

do_install() {
    set -e
    make DESTDIR="${DESTDIR}" install
}
