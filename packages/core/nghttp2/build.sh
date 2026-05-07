#!/bin/bash
# nghttp2 1.68.1 — HTTP/2 C library
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr     \
                --disable-static  \
                --enable-lib-only \
                --docdir=/usr/share/doc/nghttp2-${PKG_VERSION}
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    make check || true
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
