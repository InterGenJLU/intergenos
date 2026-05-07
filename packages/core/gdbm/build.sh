#!/bin/bash
# GDBM 1.26
# LFS 13.0 Section 8.39

configure() {
    set -e
    ./configure --prefix=/usr    \
        --disable-static         \
        --enable-libgdbm-compat
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
