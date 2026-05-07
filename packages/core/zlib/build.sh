#!/bin/bash
# Zlib 1.3.2
# LFS 13.0 Section 8.6

configure() {
    set -e
    ./configure --prefix=/usr
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
    rm -fv "${DESTDIR}/usr/lib/libz.a"
}
