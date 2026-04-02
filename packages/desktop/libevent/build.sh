#!/bin/bash
# libevent 2.1.12 — Event notification library
# BLFS 13.0

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
