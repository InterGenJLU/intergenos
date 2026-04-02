#!/bin/bash
# libssh2 1.11.1 — SSH2 client library
# BLFS 13.0

configure() {
    ./configure --prefix=/usr          \
                --disable-docker-tests \
                --disable-static
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
