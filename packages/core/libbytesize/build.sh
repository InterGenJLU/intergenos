#!/bin/bash
# libbytesize 2.12 — Library for operations with sizes in bytes
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
