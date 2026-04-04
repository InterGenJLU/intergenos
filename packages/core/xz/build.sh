#!/bin/bash
# Xz Utils 5.8.2
# LFS 13.0 Section 8.8

configure() {
    ./configure --prefix=/usr    \
        --disable-static         \
        --docdir=/usr/share/doc/xz-5.8.2
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make check || true
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
