#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# sassc 3.6.2 — SASS CSS preprocessor compiler
# BLFS 13.0

configure() {
    set -e
    # Build and install libsass first
    tar -xf "${IGOS_SOURCES}/libsass-3.6.6.tar.gz"
    cd libsass-3.6.6
    autoreconf -fi
    ./configure --prefix=/usr --disable-static
    make -j${IGOS_JOBS}
    # Install libsass to live filesystem — must unset DESTDIR which
    # the builder exports, otherwise autotools picks it up from env
    env -u DESTDIR make install
    ldconfig
    cd ..

    # Now configure sassc
    autoreconf -fi
    ./configure --prefix=/usr
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    # libsass was installed live in configure() for the sassc link; it must
    # ALSO stage into the archive or installed systems get a sassc binary
    # with no libsass.so.1 (caught by the archive-seal gate, ge9b-06).
    make -C libsass-3.6.6 DESTDIR="$DESTDIR" install
    make DESTDIR="$DESTDIR" install
}
