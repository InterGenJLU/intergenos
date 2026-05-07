#!/bin/bash
# Less 692
# LFS 13.0 Section 8.43

configure() {
    set -e
    ./configure --prefix=/usr --sysconfdir=/etc
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
