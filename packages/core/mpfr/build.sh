#!/bin/bash
# MPFR 4.2.2
# LFS 13.0 Section 8.23

configure() {
    ./configure --prefix=/usr        \
        --disable-static             \
        --enable-thread-safe         \
        --docdir=/usr/share/doc/mpfr-4.2.2
}

build() {
    make -j${IGOS_JOBS}
    make html
}

check() {
    make check
}

install() {
    make DESTDIR="$DESTDIR" install
    make DESTDIR="$DESTDIR" install-html
}
