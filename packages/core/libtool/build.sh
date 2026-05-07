#!/bin/bash
# Libtool 2.5.4
# LFS 13.0 Section 8.38

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
    rm -fv "${DESTDIR}/usr/lib/libltdl.a"
}
