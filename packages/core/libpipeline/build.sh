#!/bin/bash
# Libpipeline 1.5.8
# LFS 13.0 Section 8.70

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
