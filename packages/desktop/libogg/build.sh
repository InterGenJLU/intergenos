#!/bin/bash
# libogg 1.3.6 — Ogg bitstream container library
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --disable-static \
                --docdir=/usr/share/doc/libogg-${version}
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
