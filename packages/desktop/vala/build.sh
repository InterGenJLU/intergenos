#!/bin/bash
# vala 0.56.18 — Vala programming language compiler
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    set -e
    # BLFS: make bootstrap rebuilds the compiler using itself
    make bootstrap -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
