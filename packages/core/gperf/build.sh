#!/bin/bash
# Gperf 3.3
# LFS 13.0 Section 8.40

configure() {
    ./configure --prefix=/usr \
        --docdir=/usr/share/doc/gperf-3.3
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
