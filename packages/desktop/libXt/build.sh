#!/bin/bash
# libXt 1.3.1 — X Toolkit Intrinsics library
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr \
                --disable-static \
                --with-appdefaultdir=/etc/X11/app-defaults
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
