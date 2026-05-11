#!/bin/bash
# libogg 1.3.6 — Ogg bitstream container library
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr \
                --disable-static \
                --docdir=/usr/share/doc/libogg-${version}
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
