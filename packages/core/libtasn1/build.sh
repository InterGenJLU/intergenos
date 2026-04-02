#!/bin/bash
# libtasn1 4.21.0 — ASN.1 library
# BLFS 13.0

configure() {
    ./configure --prefix=/usr --disable-static
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
