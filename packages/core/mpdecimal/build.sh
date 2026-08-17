#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# mpdecimal 4.0.1 — decimal floating-point arithmetic library (libmpdec).
# No LFS/BLFS chapter yet (upstream LFS holds mpdecimal until Python 3.16);
# this is the system libmpdec that Python's _decimal module links via
# ./configure --with-system-libmpdec, replacing Python's deprecated bundled
# copy. Source of record: bytereef.org. Satisfies CPython's `libmpdec >= 2.5.0`
# pkg-config gate. Full dep-tree review: no build/runtime deps beyond the base
# toolchain (configure.ac has no AC_CHECK_LIB / PKG_CHECK_MODULES).

configure() {
    set -e
    ./configure --prefix=/usr \
        --disable-static
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    # Offline-safe local suite. The default `make check` downloads upstream
    # test vectors over the network (unavailable in the build chroot);
    # check_local runs the bundled local test cases instead.
    make check_local
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
