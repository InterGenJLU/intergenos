#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# libfreeaptx 0.2.2 — the free aptX / aptX HD encoder and decoder the
# Bluetooth audio stack looks up as the pkg-config module `libfreeaptx`.
#
# The upstream build is a POSIX Makefile with no configure step: PREFIX
# defaults to /usr/local, so it is set here for every phase. The default
# target builds the shared library, its two symlinks and the two utilities;
# the `all` target would additionally build statically linked copies of the
# utilities, which this system does not ship.
#
# CC is set explicitly: a .POSIX: Makefile defaults CC to `c99`, and this
# system ships no c99 command, so the untouched build halts with
# "make: c99: No such file or directory" (measured 2026-09-19).

build() {
    set -e
    make CC=gcc PREFIX=/usr LIBDIR=lib -j${IGOS_JOBS}
}

do_install() {
    set -e
    make CC=gcc PREFIX=/usr LIBDIR=lib DESTDIR="$DESTDIR" install
}
