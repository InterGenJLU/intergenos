#!/bin/bash
# soundtouch 2.4.0 — Audio tempo/pitch processing library
# BLFS 13.0

configure() {
    set -e
    ./bootstrap
    ./configure --prefix=/usr \
                --docdir=/usr/share/doc/soundtouch-2.4.0
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
