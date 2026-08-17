#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# cdparanoia 10.2 — CD audio extraction tool
# BLFS 13.0

configure() {
    set -e
    # cdparanoia uses -fpic (lowercase) which is insufficient for shared
    # libraries on x86_64 with GCC 15. Force -fPIC via CFLAGS.
    export CFLAGS="-O2 -fPIC"
    ./configure --prefix=/usr
}

build() {
    set -e
    # SERIAL build (-j1) — cdparanoia's hand-written 2008-era Makefile is NOT
    # parallel-safe: interface/scsi_interface.c is compiled twice to the SAME
    # object (once without -fpic for libcdda_interface.a, once with -fpic for
    # the .so). Under make -jN those compiles + the .so link run concurrently,
    # so the shared lib can link a half-written/wrong scsi_interface.o and end
    # up with undefined scsi_read_mmc2/scsi_init_drive/scsi_inquiry (caught only
    # at the final cdparanoia exe link). Nondeterministic — surfaced on the first
    # from-scratch 16-core rebuild (2026-06-03). Same class as linux-firmware's
    # NUM_JOBS=1. Proven: -j1 links clean, -j16 races. Do NOT restore -j${IGOS_JOBS}.
    make -j1
}

do_install() {
    set -e
    # cdparanoia's Makefile has no DESTDIR support — redirect install
    # paths manually to the staging directory
    make install \
        BINDIR="${DESTDIR}/usr/bin" \
        MANDIR="${DESTDIR}/usr/share/man" \
        INCLUDEDIR="${DESTDIR}/usr/include/cdda" \
        LIBDIR="${DESTDIR}/usr/lib"

}
