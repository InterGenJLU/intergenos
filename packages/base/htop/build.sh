#!/bin/bash
# htop 3.4.1 — Interactive process viewer
# BLFS 13.0

configure() {
    ./configure --prefix=/usr
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
