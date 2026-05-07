#!/bin/bash
# Attr 2.5.2
# LFS 13.0 Section 8.25

configure() {
    set -e
    ./configure --prefix=/usr     \
        --disable-static          \
        --sysconfdir=/etc         \
        --docdir=/usr/share/doc/attr-2.5.2
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
