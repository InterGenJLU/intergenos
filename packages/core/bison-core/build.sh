#!/bin/bash
# Bison 3.8.2
# LFS 13.0 Section 8.35

configure() {
    ./configure --prefix=/usr \
        --docdir=/usr/share/doc/bison-3.8.2
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make check
}

install() {
    make DESTDIR="$DESTDIR" install
}
