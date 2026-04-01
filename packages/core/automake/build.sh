#!/bin/bash
# Automake 1.18.1
# LFS 13.0 Section 8.48

configure() {
    ./configure --prefix=/usr \
        --docdir=/usr/share/doc/automake-1.18.1
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    # Use at least 4 parallel jobs — Automake tests have internal delays
    make -j$(($(nproc)>4?$(nproc):4)) check
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
