#!/bin/bash
# Gperf 3.3
# LFS 13.0 Section 8.40

configure() {
    set -e
    ./configure --prefix=/usr \
        --docdir=/usr/share/doc/gperf-3.3
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
