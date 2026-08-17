#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# atop 2.12.1 — Advanced system and process monitor
# From upstream (not in BLFS)

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    # Upstream Makefile sets SYSDPATH=/lib/systemd/system which creates a bare
    # lib/ dir in DESTDIR — on LFS systems /lib is a symlink to usr/lib, and
    # deploying a real lib/ directory over that symlink kills the dynamic linker.
    # Override to the correct FHS/systemd path.
    make DESTDIR="$DESTDIR" SYSDPATH=/usr/lib/systemd/system install
}
