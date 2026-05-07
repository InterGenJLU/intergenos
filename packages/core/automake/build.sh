#!/bin/bash
# Automake 1.18.1
# LFS 13.0 Section 8.48

configure() {
    set -e
    ./configure --prefix=/usr \
        --docdir=/usr/share/doc/automake-1.18.1
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    # Use at least 4 parallel jobs — Automake tests have internal delays
    make -j$(($(nproc)>4?$(nproc):4)) check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
