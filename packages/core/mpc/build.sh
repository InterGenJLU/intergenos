#!/bin/bash
# MPC 1.3.1
# LFS 13.0 Section 8.24

configure() {
    set -e
    ./configure --prefix=/usr    \
        --disable-static         \
        --docdir=/usr/share/doc/mpc-1.3.1
}

build() {
    set -e
    make -j${IGOS_JOBS}
    make html
}

check() {
    set -e
    make check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
    make DESTDIR="$DESTDIR" install-html
}
