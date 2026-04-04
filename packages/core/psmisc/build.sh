#!/bin/bash
# Psmisc 23.7
# LFS 13.0 Section 8.33

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
