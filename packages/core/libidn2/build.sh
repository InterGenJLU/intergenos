#!/bin/bash
# libidn2 2.3.8 — Internationalized domain names library
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
