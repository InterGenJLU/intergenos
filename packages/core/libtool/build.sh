#!/bin/bash
# Libtool 2.5.4
# LFS 13.0 Section 8.38

configure() {
    ./configure --prefix=/usr
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make check
}

install() {
    make DESTDIR="$DESTDIR" install
    rm -fv "${DESTDIR}/usr/lib/libltdl.a"
}
