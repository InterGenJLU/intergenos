#!/bin/bash
# libuv 1.52.1 — Multi-platform async I/O
# BLFS 13.0

configure() {
    sh autogen.sh
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
