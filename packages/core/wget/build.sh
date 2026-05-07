#!/bin/bash
# wget 1.25.0 — Network file retriever
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr      \
                --sysconfdir=/etc  \
                --with-ssl=openssl
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
