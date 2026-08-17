#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# liburcu (userspace-rcu) 0.15.3 — read-copy-update lockless synchronization.
# Required build dep for xfsprogs 7.x (BLFS reference).
# Source orchestrator: extracts userspace-rcu-${version}.tar.bz2; top-level
# dir lands as userspace-rcu-${version}/.

configure() {
    set -e
    ./configure --prefix=/usr \
                --disable-static \
                --docdir=/usr/share/doc/liburcu-${version}
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
