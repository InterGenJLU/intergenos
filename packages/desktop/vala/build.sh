#!/bin/bash
# vala 0.56.18 — Vala programming language compiler
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    # BLFS: make bootstrap rebuilds the compiler using itself
    make bootstrap -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
