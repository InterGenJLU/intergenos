#!/bin/bash
# iucode-tool 2.3.1 — Intel processor microcode management tool
# BLFS 13.0

configure() {
    autoreconf -fi

    ./configure --prefix=/usr
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
