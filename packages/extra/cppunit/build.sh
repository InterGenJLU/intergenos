#!/bin/bash
# cppunit 1.15.1 — C++ unit testing framework
# Not in BLFS 13.0 — standard autotools build

configure() {
    set -e
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
