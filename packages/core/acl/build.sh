#!/bin/bash
# Acl 2.3.2
# LFS 13.0 Section 8.26

configure() {
    ./configure --prefix=/usr    \
        --disable-static         \
        --docdir=/usr/share/doc/acl-2.3.2
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make check
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
