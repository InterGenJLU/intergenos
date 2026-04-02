#!/bin/bash
# fdk-aac 2.0.3 — Fraunhofer FDK AAC codec
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
