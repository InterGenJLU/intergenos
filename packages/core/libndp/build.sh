#!/bin/bash
# libndp 1.9 — Neighbor Discovery Protocol library
# BLFS 13.0

configure() {
    set -e
    # GitHub archive tarball — no pre-generated configure script
    ./autogen.sh

    ./configure --prefix=/usr        \
                --sysconfdir=/etc    \
                --localstatedir=/var \
                --disable-static
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
