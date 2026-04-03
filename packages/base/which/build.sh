#!/bin/bash
# which 2.23 — Utility to show the full path of commands
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
