#!/bin/bash
# libatomic_ops 7.10.0 — Atomic memory update operations library
# BLFS 13.0

configure() {
    ./configure --prefix=/usr                                    \
                --enable-shared                                  \
                --disable-static                                 \
                --docdir=/usr/share/doc/libatomic_ops-${PKG_VERSION}
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
