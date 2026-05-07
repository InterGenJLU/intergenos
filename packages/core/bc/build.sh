#!/bin/bash
# Bc 7.0.3
# LFS 13.0 Section 8.15

configure() {
    set -e
    # CC='gcc -std=c99' per LFS — required for GCC 15 compatibility
    CC='gcc -std=c99' ./configure --prefix=/usr -G -O3 -r
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    make test
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
