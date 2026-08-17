#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# GNU parallel 20260322 — Parallel command execution
# Required by linux-firmware for compressed install

configure() {
    set -e
    ./configure --prefix=/usr
}

build() {
    set -e
    make
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
