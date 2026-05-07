#!/bin/bash
# libnsl 2.0.1 — NIS library (libnsl replacement for glibc)
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr     \
                --sysconfdir=/etc \
                --disable-static
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
