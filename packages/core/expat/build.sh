#!/bin/bash
# Expat 2.7.4
# LFS 13.0 Section 8.41

configure() {
    set -e
    ./configure --prefix=/usr    \
        --disable-static         \
        --docdir=/usr/share/doc/expat-2.7.4
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
    install -v -m644 doc/*.{html,css} "${DESTDIR}/usr/share/doc/expat-2.7.4"
}
