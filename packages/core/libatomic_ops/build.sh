#!/bin/bash
# libatomic_ops 7.10.0 — Atomic memory update operations library
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr                                    \
                --enable-shared                                  \
                --disable-static                                 \
                --docdir=/usr/share/doc/libatomic_ops-${PKG_VERSION}
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
