#!/bin/bash
# cppunit 1.15.1 — C++ unit testing framework
# Not in BLFS 13.0 — standard autotools build

configure() {
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
