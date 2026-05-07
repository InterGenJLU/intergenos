#!/bin/bash
# libuv 1.52.1 — Multi-platform async I/O
# BLFS 13.0

configure() {
    set -e
    sh autogen.sh
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
