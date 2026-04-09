#!/bin/bash
# libde265 1.0.16 — Open source H.265/HEVC video decoder
# BLFS 13.0

configure() {
    # Remove development-only tools per BLFS
    sed '/tools/d' -i Makefile.am
    ./autogen.sh

    ./configure --prefix=/usr         \
                --disable-sherlock265 \
                --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
