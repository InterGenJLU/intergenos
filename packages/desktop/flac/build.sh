#!/bin/bash
# flac 1.5.0 — Free Lossless Audio Codec
# BLFS 13.0 (uses autotools, not cmake)

configure() {
    ./configure --prefix=/usr            \
                --disable-thorough-tests \
                --docdir=/usr/share/doc/flac-${version}
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make check || true
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
