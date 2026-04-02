#!/bin/bash
# xkbcomp 1.4.7 — XKB keyboard description compiler
# BLFS 13.0

configure() {
    ./configure --prefix=/usr
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
