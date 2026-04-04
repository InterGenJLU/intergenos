#!/bin/bash
# Gzip 1.14
# LFS 13.0 Section 8.67

configure() {
    ./configure --prefix=/usr
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
