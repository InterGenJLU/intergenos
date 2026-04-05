#!/bin/bash
# soundtouch 2.4.0 — Audio tempo/pitch processing library
# BLFS 13.0

configure() {
    ./bootstrap
    ./configure --prefix=/usr \
                --docdir=/usr/share/doc/soundtouch-2.4.0
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
