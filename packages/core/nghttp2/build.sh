#!/bin/bash
# nghttp2 1.68.1 — HTTP/2 C library
# BLFS 13.0

configure() {
    ./configure --prefix=/usr     \
                --disable-static  \
                --enable-lib-only \
                --docdir=/usr/share/doc/nghttp2-${PKG_VERSION}
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make check || true
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
