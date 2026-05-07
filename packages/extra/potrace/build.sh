#!/bin/bash
# potrace 1.16 — Bitmap to vector graphics conversion
# BLFS 13.0

configure() {
    set -e
    CC=gcc ./configure --prefix=/usr                             \
                --disable-static                                 \
                --docdir=/usr/share/doc/potrace-${PKG_VERSION}   \
                --enable-a4                                      \
                --enable-metric                                  \
                --with-libpotrace
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    make check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
