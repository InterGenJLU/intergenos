#!/bin/bash
# potrace 1.16 — Bitmap to vector graphics conversion
# BLFS 13.0

configure() {
    CC=gcc ./configure --prefix=/usr                             \
                --disable-static                                 \
                --docdir=/usr/share/doc/potrace-${PKG_VERSION}   \
                --enable-a4                                      \
                --enable-metric                                  \
                --with-libpotrace
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make check
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
