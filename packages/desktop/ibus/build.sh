#!/bin/bash
# ibus 1.5.31 — Intelligent Input Bus framework
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --sysconfdir=/etc
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
