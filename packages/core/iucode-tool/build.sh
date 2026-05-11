#!/bin/bash
# iucode-tool 2.3.1 — Intel processor microcode management tool
# BLFS 13.0

configure() {
    set -e
    autoreconf -fi

    ./configure --prefix=/usr
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
