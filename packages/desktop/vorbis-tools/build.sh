#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# vorbis-tools 1.4.3 — Vorbis codec command-line tools (oggenc, oggdec, etc.)
# Required by: gnome-clocks (alarm sound .ogg encoding at build time)

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
