#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# lvm2 2.03.38 — Logical Volume Manager
# BLFS 13.0

configure() {
    set -e
    PATH+=:/usr/sbin                \
    ./configure --prefix=/usr       \
                --enable-cmdlib     \
                --enable-pkgconfig  \
                --enable-udev_sync
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
    make DESTDIR="$DESTDIR" install_systemd_units
}
