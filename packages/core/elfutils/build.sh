#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Elfutils 0.194 (Libelf + Libdw)
# LFS 13.0 Section 8.50 (libelf), extended with libdw
#
# Decided 2026-08-15: install libdw alongside libelf. libcamera pins
# -Dlibdw=enabled (call-stack backtraces via DWARF) and libdw is the
# canonical provider; the library is already compiled by this package's
# own source tree. Additive to the LFS libelf-only baseline — libelf
# install is unchanged. Since elfutils 0.178 the ebl backends link into
# libdw.so directly, so no separate backend modules are installed.

configure() {
    set -e
    ./configure --prefix=/usr        \
        --disable-debuginfod         \
        --enable-libdebuginfod=dummy
}

build() {
    set -e
    # Full-tree build: libdw.so links objects from libdwelf/, libdwfl/,
    # libebl/, backends/ — the top-level Makefile owns that ordering.
    # -Wno-error: this release hardwires -Werror in config/eu.am (no
    # configure switch exists), and GCC 15 raises new warnings across the
    # tree. Automake puts user CFLAGS after AM_CFLAGS, so the appended
    # -Wno-error wins; every warning still prints in the build log.
    make -j${IGOS_JOBS} CFLAGS="${CFLAGS} -Wno-error"
}

check() {
    set -e
    : # Test suite fails to build with glibc-2.43+, skip
}

do_install() {
    set -e
    make -C libelf  DESTDIR="$DESTDIR" install
    make -C libdw   DESTDIR="$DESTDIR" install
    # libdwfl/libdwelf code lives INSIDE libdw.so; their installs ship the
    # public headers (elfutils/libdwfl.h, elfutils/libdwelf.h) consumers
    # include — libcamera's backtrace.cpp includes libdwfl.h directly.
    make -C libdwfl  DESTDIR="$DESTDIR" install
    make -C libdwelf DESTDIR="$DESTDIR" install
    install -vDm644 config/libelf.pc "${DESTDIR}/usr/lib/pkgconfig/libelf.pc"
    install -vDm644 config/libdw.pc  "${DESTDIR}/usr/lib/pkgconfig/libdw.pc"
    rm -f "${DESTDIR}/usr/lib/libelf.a" "${DESTDIR}/usr/lib/libdw.a"
}
