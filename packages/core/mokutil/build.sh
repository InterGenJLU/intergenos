#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# mokutil — Machine Owner Key management
# Required by: Forge installer (queue_mok_enrollment)

configure() {
    set -e
    ./autogen.sh 2>/dev/null || autoreconf -fiv
    ./configure --prefix=/usr --sysconfdir=/etc
}

build() {
    set -e
    make
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
