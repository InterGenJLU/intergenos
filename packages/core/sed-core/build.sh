#!/bin/bash
# Sed 4.9
# LFS 13.0 Section 8.32

configure() {
    set -e
    ./configure --prefix=/usr
}

build() {
    set -e
    make -j${IGOS_JOBS}
    make html
}

check() {
    set -e
    chown -R tester .
    su tester -c "PATH=$PATH make check"
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
    install -d -m755 "${DESTDIR}/usr/share/doc/sed-4.9"
    install -m644 doc/sed.html "${DESTDIR}/usr/share/doc/sed-4.9"
}
