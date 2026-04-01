#!/bin/bash
# Diffutils 3.12
# LFS 13.0 Section 8.62

configure() {
    ./configure --prefix=/usr
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
