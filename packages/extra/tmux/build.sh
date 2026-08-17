#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# tmux 3.6b — Terminal multiplexer
# BLFS 13.0
#
# autotools. Detects libevent (libevent_core >= 2) and ncurses/ncursesw via
# pkg-config (configure.ac PKG_CHECK_MODULES). Both deps are in the tree:
# libevent (desktop tier) and ncurses (toolchain tier). Installs /usr/bin/tmux
# and tmux.1 into ${mandir}/man1.

configure() {
    set -e
    ./configure --prefix=/usr --sysconfdir=/etc
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
