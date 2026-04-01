#!/bin/bash
# Bc 7.0.3
# LFS 13.0 Section 8.15

configure() {
    CC=gcc ./configure --prefix=/usr -G -O3 -r
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make test
}

install() {
    make DESTDIR="$DESTDIR" install
}
