#!/bin/bash
# jansson 2.15.0 — C library for JSON data
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    make
}

check() {
    make check
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
