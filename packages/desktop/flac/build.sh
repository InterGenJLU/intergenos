#!/bin/bash
# flac 1.5.0 — Free Lossless Audio Codec
# BLFS 13.0 (uses autotools, not cmake)

configure() {
    set -e
    ./configure --prefix=/usr            \
                --disable-thorough-tests \
                --docdir=/usr/share/doc/flac-${version}
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    make check || true
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
