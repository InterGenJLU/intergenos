#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# sshpass 1.10 — Non-interactive ssh password provider
#
# Tiny single-file autotools program (main.c). No library dependencies.
# Installs /usr/bin/sshpass and the sshpass.1 man page.

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
