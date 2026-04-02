#!/bin/bash
# lsof 4.99.6 — List open files
# BLFS 13.0

configure() {
    ./configure --prefix=/usr --disable-static
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
