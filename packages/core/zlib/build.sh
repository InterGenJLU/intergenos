#!/bin/bash
# Zlib 1.3.2
# LFS 13.0 Section 8.6

configure() {
    ./configure --prefix=/usr
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make check || true
}

do_install() {
    make DESTDIR="$DESTDIR" install
    rm -fv "${DESTDIR}/usr/lib/libz.a"
}
