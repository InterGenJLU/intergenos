#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# lm-sensors 3.6.0 — libsensors + the sensors CLI + sensors-detect +
# fancontrol/pwmconfig. Plain hand-written Makefile (no configure).
# Build variables (verified against the 3.6.0 Makefile, defaults in
# parentheses): PREFIX (/usr/local), MANDIR ($(PREFIX)/man),
# BUILD_STATIC_LIB (1 — off here, shared only), EXLDFLAGS
# (-Wl,-rpath,$(LIBDIR) — emptied here: no rpath in shipped binaries;
# libsensors lives in the default linker path).

configure() {
    set -e
    :
}

build() {
    set -e
    make PREFIX=/usr MANDIR=/usr/share/man BUILD_STATIC_LIB=0 EXLDFLAGS= \
         -j"${IGOS_JOBS:-1}"
}

do_install() {
    set -e
    make PREFIX=/usr MANDIR=/usr/share/man BUILD_STATIC_LIB=0 EXLDFLAGS= \
         DESTDIR="$DESTDIR" install
}
