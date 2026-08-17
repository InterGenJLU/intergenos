#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# mtools 4.0.49 — access FAT (MS-DOS) filesystems without mounting them.
# BLFS-style autotools build. A single `mtools` binary is built and the
# per-command names (mcopy, mmd, mformat, mdir, mlabel, ...) are installed as
# links to it. No external library dependencies in this configuration
# (X11/floppyd left disabled by default).

configure() {
    set -e
    ./configure --prefix=/usr
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
