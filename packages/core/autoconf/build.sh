#!/bin/bash
# Autoconf 2.72
# LFS 13.0 Section 8.47

configure() {
    ./configure --prefix=/usr
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make check
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
