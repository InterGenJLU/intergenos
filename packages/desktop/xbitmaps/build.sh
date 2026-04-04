#!/bin/bash
# xbitmaps 1.1.3 — X11 bitmap files
# BLFS 13.0

configure() {
    ./configure --prefix=/usr
                --sysconfdir=/etc \
                --localstatedir=/var \
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
