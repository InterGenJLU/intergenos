#!/bin/bash
# Attr 2.5.2
# LFS 13.0 Section 8.25

configure() {
    ./configure --prefix=/usr     \
        --disable-static          \
        --sysconfdir=/etc         \
        --docdir=/usr/share/doc/attr-2.5.2
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
