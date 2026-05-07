#!/bin/bash
# Libffi 3.5.2
# LFS 13.0 Section 8.51

configure() {
    set -e
    ./configure --prefix=/usr    \
        --disable-static         \
        --with-gcc-arch=native
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    make check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
