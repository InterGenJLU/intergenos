#!/bin/bash
# libtasn1 4.21.0 — ASN.1 library
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr --disable-static
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
