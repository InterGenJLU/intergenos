#!/bin/bash
# MPFR 4.2.2
# LFS 13.0 Section 8.23

configure() {
    set -e
    ./configure --prefix=/usr        \
        --disable-static             \
        --enable-thread-safe         \
        --docdir=/usr/share/doc/mpfr-4.2.2
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
