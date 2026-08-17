#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# ghostscript 10.06.0 — Interpreter for PostScript and PDF
# BLFS 13.0

configure() {
    set -e
    # Remove bundled copies of libraries we have system versions of
    rm -rf freetype lcms2mt jpeg libpng openjpeg zlib

    ./configure --prefix=/usr           \
                --disable-compile-inits \
                --with-system-libtiff   \
                CFLAGS="${CFLAGS:--g -O3} -fPIC"
}

build() {
    set -e
    make -j${IGOS_JOBS}
    # Build shared library (libgs.so)
    make so
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
    make DESTDIR="$DESTDIR" soinstall

    # Install headers for programs that link to libgs
    install -v -m644 base/*.h "${DESTDIR}/usr/include/ghostscript/"
    ln -sfvn ghostscript "${DESTDIR}/usr/include/ps"

    # Doc directory rename staged (hook-contract wave; the retired hook did
    # this mv on every live target).
    if [ -d "${DESTDIR}/usr/share/doc/ghostscript/${PKG_VERSION}" ]; then
        mv "${DESTDIR}/usr/share/doc/ghostscript/${PKG_VERSION}" \
           "${DESTDIR}/usr/share/doc/ghostscript-${PKG_VERSION}"
        rmdir "${DESTDIR}/usr/share/doc/ghostscript" 2>/dev/null || true
    fi
}

