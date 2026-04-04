#!/bin/bash
# gnupg2 2.5.17 — GNU Privacy Guard
# BLFS 13.0

configure() {
    ./configure --prefix=/usr \
                --localstatedir=/var \
                --sysconfdir=/etc
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
