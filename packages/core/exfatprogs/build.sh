#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# exfatprogs 1.4.2 — exFAT filesystem userspace utilities.
# Installs mkfs.exfat / fsck.exfat / exfatlabel / dump.exfat / tune.exfat /
# exfat2img (/usr/sbin) + lsdosattr / chdosattr (/usr/bin). Links libblkid
# (util-linux). Runtime dependency of GParted for exFAT format/check support.
# Upstream ships a pre-generated configure (autotools dist tarball).

configure() {
    set -e
    ./configure --prefix=/usr --disable-static
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
