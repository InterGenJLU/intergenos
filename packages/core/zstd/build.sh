#!/bin/bash
# Zstd 1.5.7
# LFS 13.0 Section 8.10
#
# DESTDIR exception: needs both prefix and DESTDIR.

configure() {
    set -e
    : # Plain Makefile, no configure
}

build() {
    set -e
    make prefix=/usr -j${IGOS_JOBS}
}

check() {
    set -e
    make check
}

do_install() {
    set -e
    make prefix=/usr DESTDIR="$DESTDIR" install
    rm -v "${DESTDIR}/usr/lib/libzstd.a"
}
