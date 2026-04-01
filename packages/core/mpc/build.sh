#!/bin/bash
# MPC 1.3.1
# LFS 13.0 Section 8.24

configure() {
    ./configure --prefix=/usr    \
        --disable-static         \
        --docdir=/usr/share/doc/mpc-1.3.1
}

build() {
    make -j${IGOS_JOBS}
    make html
}

check() {
    make check
}

do_install() {
    make DESTDIR="$DESTDIR" install
    make DESTDIR="$DESTDIR" install-html
}
