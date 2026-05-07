#!/bin/bash
# DejaGNU 1.6.3
# LFS 13.0 Section 8.19
#
# Build in a dedicated directory. No separate make step.

configure() {
    set -e
    mkdir -v build
    cd       build
    ../configure --prefix=/usr
}

build() {
    set -e
    cd build
    makeinfo --html --no-split -o doc/dejagnu.html ../doc/dejagnu.texi
    makeinfo --plaintext       -o doc/dejagnu.txt  ../doc/dejagnu.texi
}

check() {
    set -e
    cd build
    make check
}

do_install() {
    set -e
    cd build
    make DESTDIR="$DESTDIR" install
    install -v -dm755 "${DESTDIR}/usr/share/doc/dejagnu-1.6.3"
    install -v -m644  doc/dejagnu.{html,txt} "${DESTDIR}/usr/share/doc/dejagnu-1.6.3"
}
