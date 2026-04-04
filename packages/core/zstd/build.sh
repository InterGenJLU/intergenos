#!/bin/bash
# Zstd 1.5.7
# LFS 13.0 Section 8.10
#
# DESTDIR exception: needs both prefix and DESTDIR.

configure() {
    : # Plain Makefile, no configure
}

build() {
    make prefix=/usr -j${IGOS_JOBS}
}

check() {
    make check || true
}

do_install() {
    make prefix=/usr DESTDIR="$DESTDIR" install
    rm -v "${DESTDIR}/usr/lib/libzstd.a"
}
