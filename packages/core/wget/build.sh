#!/bin/bash
# wget 1.25.0 — Network file retriever
# BLFS 13.0

configure() {
    ./configure --prefix=/usr      \
                --sysconfdir=/etc  \
                --with-ssl=openssl
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
